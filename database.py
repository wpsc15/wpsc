import sqlite3
import os
import re
from pathlib import Path
from passlib.context import CryptContext
from datetime import datetime
from config import DB_PATH, DEFAULT_ADMIN_USER, DEFAULT_ADMIN_PASS

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def get_db() -> sqlite3.Connection:
    """Return a sqlite3 connection with row_factory set to dict-like access."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables and seed default admin user if not present."""
    conn = get_db()
    cur = conn.cursor()

    # Users table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'admin',
            allowed_domains TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    user_columns = {row["name"] for row in cur.execute("PRAGMA table_info(users)").fetchall()}
    if "role" not in user_columns:
        cur.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
    if "allowed_domains" not in user_columns:
        cur.execute("ALTER TABLE users ADD COLUMN allowed_domains TEXT NOT NULL DEFAULT ''")
    if "email" not in user_columns:
        cur.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique "
        "ON users(email COLLATE NOCASE) WHERE email != ''"
    )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user "
        "ON password_reset_tokens(user_id)"
    )

    # Audit log table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            ip TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS monitored_websites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            status_code INTEGER,
            response_time_ms INTEGER,
            is_up INTEGER NOT NULL DEFAULT 0,
            last_checked TEXT,
            error TEXT NOT NULL DEFAULT '',
            vhost_path TEXT NOT NULL DEFAULT '',
            document_root TEXT NOT NULL DEFAULT '',
            vhost_domain TEXT NOT NULL DEFAULT ''
        )
    """)

    website_columns = {
        row["name"]
        for row in cur.execute("PRAGMA table_info(monitored_websites)").fetchall()
    }
    if "vhost_path" not in website_columns:
        cur.execute(
            "ALTER TABLE monitored_websites ADD COLUMN vhost_path TEXT NOT NULL DEFAULT ''"
        )
    if "document_root" not in website_columns:
        cur.execute(
            "ALTER TABLE monitored_websites ADD COLUMN document_root TEXT NOT NULL DEFAULT ''"
        )
    if "vhost_domain" not in website_columns:
        cur.execute(
            "ALTER TABLE monitored_websites ADD COLUMN vhost_domain TEXT NOT NULL DEFAULT ''"
        )

    conn.commit()

    # Seed default admin
    existing = cur.execute(
        "SELECT id FROM users WHERE username = ?", (DEFAULT_ADMIN_USER,)
    ).fetchone()
    if not existing:
        cur.execute(
            "INSERT INTO users (username, password, role, allowed_domains) VALUES (?, ?, ?, ?)",
            (DEFAULT_ADMIN_USER, hash_password(DEFAULT_ADMIN_PASS), "admin", ""),
        )
        conn.commit()
        print(f"[WebPanel] Created default admin user: {DEFAULT_ADMIN_USER}")
    else:
        cur.execute(
            "UPDATE users SET role = 'admin' WHERE username = ? AND (role IS NULL OR role = '')",
            (DEFAULT_ADMIN_USER,),
        )
        conn.commit()

    conn.close()


def normalize_allowed_domains(value: str) -> list[str]:
    """Return a normalized list of domains from a comma-separated value."""
    if not value:
        return []
    domains = []
    for item in str(value).split(","):
        cleaned = item.strip().lower().lstrip("*.")
        if cleaned:
            domains.append(cleaned)
    return list(dict.fromkeys(domains))


def normalize_email(value: str) -> str:
    """Validate and normalize an email address used for account recovery."""
    email = (value or "").strip().lower()
    if (
        len(email) > 254
        or not re.fullmatch(r"[^@\s]{1,64}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}", email)
    ):
        raise ValueError("Enter a valid email address.")
    return email


def set_user_email(username: str, email: str) -> None:
    normalized_email = normalize_email(email)
    conn = get_db()
    try:
        conn.execute("UPDATE users SET email = ? WHERE username = ?", (normalized_email, username))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("That email address is already associated with another account.") from exc
    finally:
        conn.close()


def create_password_reset_token(email: str, token_hash: str) -> dict | None:
    """Create one short-lived reset token, limiting repeat requests per account."""
    normalized_email = normalize_email(email)
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute(
            "SELECT id, username, email FROM users WHERE email = ? COLLATE NOCASE",
            (normalized_email,),
        ).fetchone()
        if not user:
            conn.rollback()
            return None

        recent = conn.execute(
            """
            SELECT 1 FROM password_reset_tokens
            WHERE user_id = ? AND created_at > datetime('now', '-1 minute')
            """,
            (user["id"],),
        ).fetchone()
        if recent:
            conn.rollback()
            return None

        conn.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (user["id"],))
        conn.execute(
            """
            INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
            VALUES (?, ?, datetime('now', '+30 minutes'))
            """,
            (user["id"], token_hash),
        )
        conn.commit()
        return dict(user)
    finally:
        conn.close()


def clear_password_reset_token(token_hash: str) -> None:
    conn = get_db()
    try:
        conn.execute("DELETE FROM password_reset_tokens WHERE token_hash = ?", (token_hash,))
        conn.commit()
    finally:
        conn.close()


def consume_password_reset_token(token_hash: str, password_hash: str) -> str | None:
    """Atomically change a password using a valid, unexpired, one-time token."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT users.username, password_reset_tokens.user_id
            FROM password_reset_tokens
            JOIN users ON users.id = password_reset_tokens.user_id
            WHERE password_reset_tokens.token_hash = ?
              AND password_reset_tokens.expires_at > datetime('now')
            """,
            (token_hash,),
        ).fetchone()
        if not row:
            conn.rollback()
            return None

        conn.execute("UPDATE users SET password = ? WHERE id = ?", (password_hash, row["user_id"]))
        conn.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (row["user_id"],))
        conn.commit()
        return row["username"]
    finally:
        conn.close()


def user_has_domain_access(username: str, domain: str) -> bool:
    """Check whether a user is allowed to manage the supplied domain."""
    user = get_user(username)
    if not user:
        return False
    if user.get("role") == "admin":
        return True

    target = str(domain or "").strip().lower()
    allowed = normalize_allowed_domains(user.get("allowed_domains") or "")
    if not target or not allowed:
        return False

    for allowed_domain in allowed:
        if target == allowed_domain or target.endswith(f".{allowed_domain}"):
            return True
    return False


def create_user_account(
    username: str,
    password: str,
    role: str = "restricted",
    allowed_domains: str = "",
    email: str = "",
) -> dict:
    """Create a user with either admin or domain-limited privileges."""
    clean_username = (username or "").strip()
    if len(clean_username) < 3:
        raise ValueError("Username must be at least 3 characters long.")
    if len(password or "") < 12:
        raise ValueError("Password must be at least 12 characters long.")
    normalized_email = normalize_email(email)

    normalized_role = "admin" if role == "admin" else "restricted"
    normalized_domains = ""
    if normalized_role == "restricted":
        domains = normalize_allowed_domains(allowed_domains)
        if not domains:
            raise ValueError("Restricted users must include at least one allowed domain.")
        normalized_domains = ", ".join(domains)

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (clean_username,)
        ).fetchone()
        if existing:
            raise ValueError("That username already exists.")

        conn.execute(
            """
            INSERT INTO users (username, password, role, allowed_domains, email)
            VALUES (?, ?, ?, ?, ?)
            """,
            (clean_username, hash_password(password), normalized_role, normalized_domains, normalized_email),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (clean_username,)
        ).fetchone()
        return dict(row)
    except sqlite3.IntegrityError as exc:
        if "users.email" in str(exc):
            raise ValueError("That email address is already associated with another account.") from exc
        if "users.username" in str(exc):
            raise ValueError("That username already exists.") from exc
        raise
    finally:
        conn.close()


def list_users() -> list[dict]:
    """Return all user records for admin management screens."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_user(username: str) -> dict | None:
    """Fetch a user record by username. Returns dict or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def log_action(user: str, action: str, detail: str, ip: str):
    """Insert an audit log entry."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO audit_log (user, action, detail, ip) VALUES (?, ?, ?, ?)",
            (user, action, detail or "", ip or ""),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[WebPanel] Audit log error: {e}")
