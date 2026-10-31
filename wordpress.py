import io
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path, PurePosixPath

from services.php_svc import get_installed_php_versions
from services.system import service_status
from services.domains import validate_domain
from services.mysql_svc import create_database, create_user, drop_database, drop_user
from services.websites import add_website, create_apache_vhost, delete_website, set_website_vhost


WEB_ROOT = Path("/var/www")
BACKUP_ROOT = Path("/var/backups/webpanel/wordpress")
MAX_WORDPRESS_DOWNLOAD = 100 * 1024 * 1024
MAX_WORDPRESS_EXPANDED = 500 * 1024 * 1024
MAX_WORDPRESS_FILES = 10000
BACKUP_FILENAME_PATTERN = re.compile(
    r"^wp-[a-z0-9.-]+-\d{8}-\d{6}\.tar\.gz$"
)
WORDPRESS_VERSION_PATTERN = re.compile(r"^(?:latest|\d+\.\d+(?:\.\d+)?)$")
WORDPRESS_LOCALES = ("en_US", "en_GB", "es_ES", "fr_FR", "de_DE", "it_IT", "pt_BR", "hi_IN")


def available_php_fpm_versions() -> list[str]:
    versions = []
    for version in get_installed_php_versions():
        socket_path = Path(f"/run/php/php{version}-fpm.sock")
        if socket_path.exists() and not socket_path.is_symlink() and service_status(f"php{version}-fpm") == "active":
            versions.append(version)
    return versions


def install_wordpress(
    domain: str,
    root_password: str = "",
    site_title: str = "",
    admin_email: str = "",
    php_version: str = "",
    wordpress_version: str = "latest",
    language: str = "en_US",
) -> dict[str, str]:
    """Install and initialize WordPress with an isolated database and Apache site."""
    domain = validate_domain(domain)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        raise PermissionError("Run WebPanel as root to install WordPress.")
    site_title = site_title.strip()
    if not site_title or len(site_title) > 120:
        raise ValueError("Website title must be between 1 and 120 characters.")
    email_address = parseaddr(admin_email.strip())[1]
    if (
        len(admin_email) > 254
        or email_address != admin_email.strip()
        or "@" not in email_address
        or "." not in email_address.rsplit("@", 1)[-1]
    ):
        raise ValueError("Enter a valid administrator email address.")
    if php_version and php_version not in available_php_fpm_versions():
        raise ValueError("Select an active, installed PHP-FPM version.")
    if not WORDPRESS_VERSION_PATTERN.fullmatch(wordpress_version):
        raise ValueError("Select a valid WordPress version.")
    if language not in WORDPRESS_LOCALES:
        raise ValueError("Select a supported WordPress language.")
    if not WEB_ROOT.is_dir() or WEB_ROOT.is_symlink():
        raise RuntimeError("/var/www must exist and must not be a symbolic link.")
    available_dir = Path("/etc/apache2/sites-available")
    if not available_dir.is_dir():
        raise RuntimeError("Debian/Ubuntu Apache sites were not found.")
    if not shutil.which("php") or not shutil.which("mysql"):
        raise RuntimeError("Install PHP and MySQL from Stack Installer before adding WordPress.")

    document_root = WEB_ROOT / domain
    config_path = available_dir / f"{domain}.conf"
    if document_root.exists() or document_root.is_symlink() or config_path.exists() or config_path.is_symlink():
        raise FileExistsError("A website directory or Apache virtual host already exists for this domain.")

    database = f"wp_{secrets.token_hex(8)}"
    username = f"wp{secrets.token_hex(6)}"
    password = secrets.token_urlsafe(32)
    database_created = False
    user_created = False
    website_id = None
    vhost = None
    try:
        ok, message = create_database(database, root_password)
        if not ok:
            raise RuntimeError(message or "Could not create the WordPress database.")
        database_created = True
        user_created = True
        ok, message = create_user(
            username,
            password,
            database=database,
            root_pass=root_password,
            host="localhost",
        )
        if not ok:
            raise RuntimeError(message or "Could not create the WordPress database account.")
        payload = _download_wordpress(wordpress_version)
        with tempfile.TemporaryDirectory(prefix=".webpanel-wp-", dir=WEB_ROOT) as staging:
            package_root = Path(staging) / "wordpress"
            package_root.mkdir(mode=0o750)
            _extract_wordpress(payload, package_root)
            config = _make_wp_config(package_root, database, username, password)
            config_path_temp = package_root / "wp-config.php"
            config_path_temp.write_text(config, encoding="utf-8")
            os.chmod(config_path_temp, 0o640)
            os.replace(package_root, document_root)

        website_id = add_website(
            site_title,
            f"http://{domain}",
            document_root=str(document_root),
        )
        vhost = create_apache_vhost(domain, php_version)
        (document_root / "index.html").unlink(missing_ok=True)
        set_website_vhost(
            website_id,
            vhost["domain"],
            vhost["vhost_path"],
            vhost["document_root"],
        )
        _set_wordpress_ownership(document_root)
        credentials = _initialize_wordpress(
            document_root,
            site_title,
            email_address,
            language,
            php_version,
        )
        return {
            "domain": domain,
            "url": f"http://{domain}/wp-login.php",
            "document_root": str(document_root),
            **credentials,
        }
    except (
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
        tarfile.TarError,
    ) as original_error:
        cleanup_errors = []
        if website_id is not None:
            delete_website(website_id)
        if vhost:
            try:
                _remove_apache_vhost(vhost["domain"], Path(vhost["vhost_path"]))
            except (OSError, subprocess.SubprocessError) as exc:
                cleanup_errors.append(f"Apache rollback failed: {exc}")
        if document_root.exists() and not document_root.is_symlink():
            try:
                shutil.rmtree(document_root)
            except OSError as exc:
                cleanup_errors.append(f"Website files could not be removed: {exc}")
        if user_created:
            try:
                drop_ok, drop_message = drop_user(username, "localhost", root_password)
                if not drop_ok:
                    cleanup_errors.append(f"Temporary database account cleanup failed: {drop_message}")
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                cleanup_errors.append(f"Temporary database account cleanup failed: {exc}")
        if database_created:
            try:
                drop_ok, drop_message = drop_database(database, root_password)
                if not drop_ok:
                    cleanup_errors.append(f"Temporary database cleanup failed: {drop_message}")
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                cleanup_errors.append(f"Temporary database cleanup failed: {exc}")
        if cleanup_errors:
            raise RuntimeError(
                f"{original_error} Additional cleanup problems: {'; '.join(cleanup_errors)}"
            ) from original_error
        raise


def _download_wordpress(version: str = "latest") -> bytes:
    if not WORDPRESS_VERSION_PATTERN.fullmatch(version):
        raise ValueError("Select a valid WordPress version.")
    url = (
        "https://wordpress.org/latest.tar.gz"
        if version == "latest"
        else f"https://wordpress.org/wordpress-{version}.tar.gz"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "WebPanel-WP-Toolkit/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"WordPress download returned HTTP {response.status}.")
        payload = response.read(MAX_WORDPRESS_DOWNLOAD + 1)
    if len(payload) > MAX_WORDPRESS_DOWNLOAD:
        raise RuntimeError("The WordPress download exceeded the allowed size.")
    if not payload:
        raise RuntimeError("The WordPress download was empty.")
    return payload


def _initialize_wordpress(
    document_root: Path,
    site_title: str,
    admin_email: str,
    language: str,
    php_version: str = "",
) -> dict[str, str]:
    admin_username = f"wpadmin_{secrets.token_hex(3)}"
    admin_password = secrets.token_urlsafe(24)
    bootstrap = """<?php
$settings = json_decode(file_get_contents($argv[1]), true, 512, JSON_THROW_ON_ERROR);
define('WP_INSTALLING', true);
require $argv[2] . '/wp-load.php';
require ABSPATH . 'wp-admin/includes/upgrade.php';
if (is_blog_installed()) {
    fwrite(STDERR, "This WordPress site is already initialized.\\n");
    exit(2);
}
$result = wp_install(
    $settings['title'],
    $settings['username'],
    $settings['email'],
    true,
    '',
    $settings['password'],
    $settings['language']
);
if (is_wp_error($result) || !is_blog_installed()) {
    fwrite(STDERR, is_wp_error($result) ? $result->get_error_message() : "WordPress database setup did not complete.\\n");
    exit(3);
}
"""
    try:
        with tempfile.TemporaryDirectory(prefix="webpanel-wp-setup-") as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            script_path = Path(temp_dir) / "setup.php"
            settings_path.write_text(
                json.dumps(
                    {
                        "title": site_title,
                        "username": admin_username,
                        "email": admin_email,
                        "password": admin_password,
                        "language": language,
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(settings_path, 0o600)
            script_path.write_text(bootstrap, encoding="utf-8")
            os.chmod(script_path, 0o600)
            php_binary = shutil.which(f"php{php_version}") if php_version else shutil.which("php")
            if not php_binary:
                raise RuntimeError("The selected PHP CLI executable was not found.")
            result = subprocess.run(
                [php_binary, str(script_path), str(settings_path), str(document_root)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Could not initialize WordPress: {exc}") from exc
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(output or "WordPress initialization failed.")
    return {"admin_username": admin_username, "admin_password": admin_password}


def _extract_wordpress(payload: bytes, destination: Path) -> None:
    extracted_size = 0
    file_count = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            if (
                relative.is_absolute()
                or not relative.parts
                or relative.parts[0] != "wordpress"
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise ValueError("The WordPress archive contains an unsafe path.")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("The WordPress archive contains an unsupported file type.")
            file_count += 1
            extracted_size += member.size
            if file_count > MAX_WORDPRESS_FILES or extracted_size > MAX_WORDPRESS_EXPANDED:
                raise ValueError("The WordPress archive exceeded safe extraction limits.")
            target = destination.joinpath(*relative.parts[1:])
            target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError("Could not read a file in the WordPress archive.")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            os.chmod(target, 0o640 if target.name.endswith(".php") else 0o644)
    if not (destination / "wp-includes" / "version.php").is_file():
        raise RuntimeError("The downloaded archive is missing WordPress core files.")


def _php_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _make_wp_config(root: Path, database: str, username: str, password: str) -> str:
    sample = root / "wp-config-sample.php"
    if not sample.is_file() or sample.is_symlink():
        raise RuntimeError("The WordPress archive does not include its configuration template.")
    content = sample.read_text(encoding="utf-8")
    replacements = {
        "database_name_here": database,
        "username_here": username,
        "password_here": password,
        "localhost": "localhost",
    }
    for placeholder, value in replacements.items():
        content = content.replace(
            f"'{placeholder}'",
            _php_string(value),
            1,
        )
    for key in (
        "AUTH_KEY",
        "SECURE_AUTH_KEY",
        "LOGGED_IN_KEY",
        "NONCE_KEY",
        "AUTH_SALT",
        "SECURE_AUTH_SALT",
        "LOGGED_IN_SALT",
        "NONCE_SALT",
    ):
        content = re.sub(
            rf"(define\s*\(\s*['\"]{key}['\"]\s*,\s*)['\"]put your unique phrase here['\"]",
            lambda match: f"{match.group(1)}{_php_string(secrets.token_urlsafe(48))}",
            content,
            count=1,
        )
    return content


def _set_wordpress_ownership(document_root: Path) -> None:
    try:
        import grp
        import pwd

        account = pwd.getpwnam("www-data").pw_uid
        group = grp.getgrnam("www-data").gr_gid
    except (ImportError, KeyError) as exc:
        raise RuntimeError("The Apache www-data account was not found.") from exc
    for current, dirs, files in os.walk(document_root, followlinks=False):
        current_path = Path(current)
        os.chown(current_path, account, group, follow_symlinks=False)
        for name in dirs + files:
            path = current_path / name
            if not path.is_symlink():
                os.chown(path, account, group, follow_symlinks=False)


def _remove_apache_vhost(domain: str, config_path: Path) -> None:
    subprocess.run(["a2dissite", config_path.name], capture_output=True, timeout=30, check=False)
    subprocess.run(["apache2ctl", "configtest"], capture_output=True, timeout=30, check=False)
    if config_path.is_file() and not config_path.is_symlink():
        config_path.unlink()
    subprocess.run(["systemctl", "reload", "apache2"], capture_output=True, timeout=30, check=False)


def create_wordpress_backup(site: dict) -> Path:
    """Create a private archive containing wp-content, configuration, and a database dump."""
    root = Path(site["root"])
    if root.is_symlink() or not root.is_dir():
        raise ValueError("The WordPress document root is unavailable.")
    canonical_root = root.resolve(strict=True)
    if not canonical_root.is_relative_to(WEB_ROOT.resolve(strict=True)):
        raise ValueError("The WordPress installation is outside /var/www.")
    database, username, password, host = _read_database_config(canonical_root / "wp-config.php")
    if not shutil.which("mysqldump"):
        raise RuntimeError("mysqldump is not installed; install the MySQL server tools first.")
    if BACKUP_ROOT.is_symlink():
        raise ValueError("The WordPress backup directory must not be a symbolic link.")
    BACKUP_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(BACKUP_ROOT, 0o700)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"wp-{site['domain']}-{stamp}.tar.gz"
    final_path = BACKUP_ROOT / filename
    temp_dir = Path(tempfile.mkdtemp(prefix=".wp-backup-", dir=BACKUP_ROOT))
    try:
        dump_path = temp_dir / "database.sql"
        environment = os.environ.copy()
        environment["MYSQL_PWD"] = password
        command = ["mysqldump", "--single-transaction", "--routines", "--events", "--triggers"]
        if host.startswith("localhost:/"):
            command.extend(["--socket", host.split(":", 1)[1]])
        else:
            server, separator, port_or_socket = host.partition(":")
            if server not in {"localhost", "127.0.0.1"}:
                command.extend(["--host", server])
            if separator and port_or_socket.isdigit():
                command.extend(["--port", port_or_socket])
            elif separator and port_or_socket.startswith("/"):
                command.extend(["--socket", port_or_socket])
        command.extend(["--user", username, f"--databases={database}"])
        with dump_path.open("wb") as output:
            result = subprocess.run(
                command,
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=900,
                env=environment,
                check=False,
            )
        if result.returncode or dump_path.stat().st_size == 0:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(error or "MySQL did not produce a database backup.")
        with tarfile.open(final_path, "w:gz") as archive:
            archive.add(dump_path, arcname="database.sql", recursive=False)
            for name in ("wp-content", "wp-config.php"):
                source = canonical_root / name
                if not source.exists() or source.is_symlink():
                    continue
                archive.add(source, arcname=name, recursive=True)
        os.chmod(final_path, 0o600)
        return final_path
    except (OSError, subprocess.SubprocessError, RuntimeError):
        final_path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _read_database_config(config_path: Path) -> tuple[str, str, str, str]:
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("The WordPress configuration file was not found.")
    content = config_path.read_text(encoding="utf-8")
    values = {}
    for key in ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST"):
        match = re.search(
            rf"define\s*\(\s*['\"]{key}['\"]\s*,\s*(['\"])((?:\\.|(?!\1).)*)\1\s*\)",
            content,
        )
        if not match:
            raise ValueError(f"Could not read {key} from wp-config.php.")
        values[key] = match.group(2).replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
    database, username, password, host = (
        values["DB_NAME"], values["DB_USER"], values["DB_PASSWORD"], values["DB_HOST"]
    )
    if not re.fullmatch(r"[A-Za-z0-9_$-]{1,64}", database):
        raise ValueError("The WordPress database name is invalid.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", username):
        raise ValueError("The WordPress database username is invalid.")
    if not host or len(host) > 255 or any(char in host for char in "\r\n\0"):
        raise ValueError("The WordPress database host is invalid.")
    server, separator, port_or_socket = host.partition(":")
    if server not in {"localhost", "127.0.0.1"}:
        raise ValueError("Backups are only supported for databases on this server.")
    if separator and not (
        port_or_socket.isdigit()
        or (
            port_or_socket.startswith(("/run/mysqld/", "/var/run/mysqld/"))
            and ".." not in Path(port_or_socket).parts
        )
    ):
        raise ValueError("The WordPress database port or socket path is invalid.")
    return database, username, password, host


def list_wordpress_backups(domain: str) -> list[Path]:
    domain = validate_domain(domain)
    if BACKUP_ROOT.is_symlink() or not BACKUP_ROOT.is_dir():
        return []
    return sorted(
        (
            path for path in BACKUP_ROOT.iterdir()
            if BACKUP_FILENAME_PATTERN.fullmatch(path.name)
            and path.name.startswith(f"wp-{domain}-")
            and path.is_file()
            and not path.is_symlink()
        ),
        key=lambda path: path.name,
        reverse=True,
    )


def find_wordpress_backup(filename: str) -> Path:
    if not BACKUP_FILENAME_PATTERN.fullmatch(filename):
        raise FileNotFoundError("WordPress backup not found.")
    path = BACKUP_ROOT / filename
    if (
        BACKUP_ROOT.is_symlink()
        or path.is_symlink()
        or not path.is_file()
        or path.parent.resolve() != BACKUP_ROOT.resolve()
    ):
        raise FileNotFoundError("WordPress backup not found.")
    return path
