import os
import re
import secrets
import subprocess
from services.system import run_cmd, service_status, service_action
from pathlib import Path

MYSQL_SERVICE = "mysql"
DATABASE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_$-]{1,64}$")
MYSQL_USER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
MYSQL_HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.:%-]{1,255}$")
SYSTEM_DATABASES = {"information_schema", "mysql", "performance_schema", "sys"}
PROTECTED_USERS = {
    "root",
    "mysql.infoschema",
    "mysql.session",
    "mysql.sys",
    "mariadb.sys",
}


def _mysql_environment(root_pass: str) -> dict[str, str]:
    environment = os.environ.copy()
    if root_pass:
        environment["MYSQL_PWD"] = root_pass
    return environment


def _run_mysql(sql: str, root_pass: str = "") -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["mysql", "--batch", "--raw", "--skip-column-names"],
            input=sql,
            capture_output=True,
            text=True,
            timeout=60,
            env=_mysql_environment(root_pass),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _quote_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def _quote_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _quote_password(value: str) -> str:
    return _quote_literal(value)


def get_mysql_config() -> tuple[str, str]:
    """Read the primary MySQL/MariaDB configuration file."""
    for candidate in (
        Path("/etc/mysql/my.cnf"),
        Path("/etc/my.cnf"),
        Path("/etc/mysql/mysql.conf.d/mysqld.cnf"),
    ):
        if candidate.is_file():
            return str(candidate), candidate.read_text(encoding="utf-8")
    raise FileNotFoundError("MySQL configuration file was not found.")


def get_mysql_status() -> dict:
    """Return MySQL/MariaDB service status and version string."""
    status = service_status(MYSQL_SERVICE)
    if status == "unknown":
        status = service_status("mariadb")

    _, version, _ = run_cmd("mysql --version 2>/dev/null")
    return {
        "status": status,
        "running": status == "active",
        "version": version.strip() if version else "Not installed",
    }


def mysql_action(action: str) -> tuple:
    """Start/stop/restart MySQL. Falls back to mariadb service name."""
    ok, msg = service_action(MYSQL_SERVICE, action)
    if not ok:
        ok, msg = service_action("mariadb", action)
    return ok, msg


def list_databases(root_pass: str = "") -> list:
    """Return a list of database names. Requires MySQL root credentials."""
    _, out, _ = _run_mysql("SHOW DATABASES;", root_pass)
    dbs = [line for line in out.split("\n") if line and line != "Database"]
    return dbs


def list_database_details(root_pass: str = "") -> list[dict[str, str | int]]:
    """Return database size, table count, and collation from information_schema."""
    sql = """
        SELECT s.SCHEMA_NAME,
               COUNT(t.TABLE_NAME),
               COALESCE(SUM(t.DATA_LENGTH + t.INDEX_LENGTH), 0),
               COALESCE(s.DEFAULT_COLLATION_NAME, '')
        FROM information_schema.SCHEMATA AS s
        LEFT JOIN information_schema.TABLES AS t
          ON t.TABLE_SCHEMA = s.SCHEMA_NAME
        GROUP BY s.SCHEMA_NAME, s.DEFAULT_COLLATION_NAME
        ORDER BY s.SCHEMA_NAME;
    """
    code, out, error = _run_mysql(sql, root_pass)
    if code != 0:
        raise RuntimeError(error or "Could not read database inventory.")
    details = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            raise RuntimeError("The database server returned an invalid inventory row.")
        name, tables, size, collation = parts
        details.append(
            {
                "name": name,
                "tables": int(tables),
                "size_bytes": int(size),
                "collation": collation,
            }
        )
    return details


def list_database_grants(root_pass: str = "") -> dict[str, list[dict[str, str]]]:
    """Return schema-level grants without exposing global account privileges."""
    code, out, error = _run_mysql(
        "SELECT Db,User,Host FROM mysql.db ORDER BY Db,User,Host;", root_pass
    )
    if code != 0:
        raise RuntimeError(error or "Could not read database permissions.")
    grants: dict[str, list[dict[str, str]]] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        database, username, host = parts
        grants.setdefault(database, []).append({"user": username, "host": host})
    return grants


def create_database(name: str, root_pass: str = "") -> tuple:
    """Create a new MySQL database. Returns (success, message)."""
    if not DATABASE_NAME_PATTERN.fullmatch(name):
        return False, "Invalid database name."
    code, out, err = _run_mysql(f"CREATE DATABASE {_quote_identifier(name)};", root_pass)
    return code == 0, err or out


def drop_database(name: str, root_pass: str = "") -> tuple:
    """Drop a MySQL database. Returns (success, message)."""
    if not DATABASE_NAME_PATTERN.fullmatch(name):
        return False, "Invalid database name."
    if name.lower() in SYSTEM_DATABASES:
        return False, "System databases are protected from deletion."
    code, out, err = _run_mysql(f"DROP DATABASE {_quote_identifier(name)};", root_pass)
    return code == 0, err or out


def list_users(root_pass: str = "") -> list:
    """Return a list of MySQL users as [{'user': ..., 'host': ...}]."""
    _, out, _ = _run_mysql("SELECT User,Host FROM mysql.user;", root_pass)
    if not out:
        return []
    result = []
    for line in out.splitlines():
        if line:
            parts = line.split("\t")
            result.append({
                "user": parts[0],
                "host": parts[1] if len(parts) > 1 else "%",
            })
    return result


def create_user(
    username: str,
    password: str,
    database: str = "",
    root_pass: str = "",
    global_admin: bool = False,
    host: str = "localhost",
) -> tuple:
    """Create a MySQL user with database-scoped or explicit global privileges."""
    if not MYSQL_USER_PATTERN.fullmatch(username):
        return False, "Invalid database username."
    if username.lower() == "root":
        return False, (
            "Do not create or change the MySQL root account here. "
            "Use a different username for phpMyAdmin."
        )
    if username.lower() in PROTECTED_USERS or not MYSQL_HOST_PATTERN.fullmatch(host):
        return False, "Invalid or protected database account/host."
    if global_admin and host != "localhost":
        return False, "Global database administrators must be restricted to localhost."
    if len(password) < 16 or len(password) > 256:
        return False, "Password must contain 16-256 characters."
    if not global_admin and database and not DATABASE_NAME_PATTERN.fullmatch(database):
        return False, "Invalid database name."
    sql = (
        f"CREATE USER {_quote_literal(username)}@{_quote_literal(host)} "
        f"IDENTIFIED BY {_quote_password(password)};"
    )
    if global_admin:
        sql += (
            f" GRANT ALL PRIVILEGES ON *.* "
            f"TO {_quote_literal(username)}@{_quote_literal(host)} WITH GRANT OPTION;"
        )
    elif database:
        sql += (
            f" GRANT ALL PRIVILEGES ON {_quote_identifier(database)}.* "
            f"TO {_quote_literal(username)}@{_quote_literal(host)};"
        )
    code, out, err = _run_mysql(sql, root_pass)
    return code == 0, err or out


def drop_user(username: str, host: str = "localhost", root_pass: str = "") -> tuple:
    """Drop a MySQL user. Returns (success, message)."""
    if not MYSQL_USER_PATTERN.fullmatch(username) or not MYSQL_HOST_PATTERN.fullmatch(host):
        return False, "Invalid database username or host."
    if username.lower() in PROTECTED_USERS:
        return False, "Protected MySQL accounts cannot be deleted."
    sql = f"DROP USER {_quote_literal(username)}@{_quote_literal(host)}; FLUSH PRIVILEGES;"
    code, out, err = _run_mysql(sql, root_pass)
    return code == 0, err or out


def set_database_permission(
    database: str,
    username: str,
    host: str,
    allow: bool,
    root_pass: str = "",
) -> tuple[bool, str]:
    """Grant/revoke privileges for one account on one non-system database."""
    if not DATABASE_NAME_PATTERN.fullmatch(database) or database.lower() in SYSTEM_DATABASES:
        return False, "Select a valid non-system database."
    if (
        not MYSQL_USER_PATTERN.fullmatch(username)
        or username.lower() in PROTECTED_USERS
        or not MYSQL_HOST_PATTERN.fullmatch(host)
    ):
        return False, "Select a valid, non-system database account and host."

    check_sql = (
        "SELECT "
        f"(SELECT COUNT(*) FROM information_schema.SCHEMATA "
        f"WHERE SCHEMA_NAME={_quote_literal(database)}), "
        f"(SELECT COUNT(*) FROM mysql.user WHERE User={_quote_literal(username)} "
        f"AND Host={_quote_literal(host)});"
    )
    code, out, error = _run_mysql(check_sql, root_pass)
    if code != 0:
        return False, error or "Could not verify the database and account."
    parts = out.split("\t")
    if len(parts) != 2 or parts != ["1", "1"]:
        return False, "The database or account does not exist on this server."

    if allow:
        sql = (
            f"GRANT ALL PRIVILEGES ON {_quote_identifier(database)}.* "
            f"TO {_quote_literal(username)}@{_quote_literal(host)};"
        )
    else:
        sql = (
            f"REVOKE ALL PRIVILEGES ON {_quote_identifier(database)}.* "
            f"FROM {_quote_literal(username)}@{_quote_literal(host)};"
        )
    code, output, error = _run_mysql(sql, root_pass)
    return code == 0, error or output


def reset_user_password(
    username: str,
    host: str,
    root_pass: str = "",
) -> tuple[bool, str, str]:
    """Set a random account password and return it only for immediate display."""
    if (
        not MYSQL_USER_PATTERN.fullmatch(username)
        or username.lower() in PROTECTED_USERS
        or not MYSQL_HOST_PATTERN.fullmatch(host)
    ):
        return False, "Invalid or protected database account.", ""
    check_sql = (
        "SELECT COUNT(*) FROM mysql.user "
        f"WHERE User={_quote_literal(username)} AND Host={_quote_literal(host)};"
    )
    code, out, error = _run_mysql(check_sql, root_pass)
    if code != 0:
        return False, error or "Could not verify the database account.", ""
    if out != "1":
        return False, "The database account does not exist on this server.", ""

    password = secrets.token_urlsafe(24)
    sql = (
        f"ALTER USER {_quote_literal(username)}@{_quote_literal(host)} "
        f"IDENTIFIED BY {_quote_password(password)};"
    )
    code, output, error = _run_mysql(sql, root_pass)
    return code == 0, error or output, password if code == 0 else ""
