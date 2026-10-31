from fastapi import APIRouter, File, Request, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import os
import secrets
import shutil
import subprocess
import threading
import time
from services.mysql_svc import (
    get_mysql_config,
    get_mysql_status,
    mysql_action,
    list_databases,
    create_database,
    drop_database,
    list_users,
    create_user,
    drop_user,
    list_database_details,
    list_database_grants,
    reset_user_password,
    set_database_permission,
    PROTECTED_USERS,
)
from config import DATABASE_BACKUP_DIR
from services.config_editor import read_config, save_config
from services.mysql_backup import (
    MAX_RESTORE_SIZE,
    backup_directory,
    create_backup,
    find_backup,
    find_database_backup,
    list_backups,
    list_database_backups,
    create_database_backup,
    restore_backup,
)
from database import log_action
from services.php_svc import check_phpmyadmin
from services.web_security import get_csrf_token, require_valid_csrf

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/mysql")
_credential_lock = threading.Lock()
_session_mysql_credentials: dict[str, tuple[str, float]] = {}
_MYSQL_CREDENTIAL_TTL = 86400


def require_auth(request: Request):
    return request.session.get("user")


def get_root_pass(request: Request) -> str:
    """Get the root password from process memory using a random session token."""
    legacy_password = request.session.pop("mysql_root_pass", "")
    token = request.session.get("mysql_root_token")
    now = time.monotonic()
    with _credential_lock:
        expired_tokens = [
            key for key, (_, expires_at) in _session_mysql_credentials.items()
            if expires_at <= now
        ]
        for expired_token in expired_tokens:
            _session_mysql_credentials.pop(expired_token, None)
        if legacy_password:
            token = token or secrets.token_urlsafe(32)
            _session_mysql_credentials[token] = (
                legacy_password,
                now + _MYSQL_CREDENTIAL_TTL,
            )
            request.session["mysql_root_token"] = token
        if not token:
            return ""
        entry = _session_mysql_credentials.get(token)
        if entry is None:
            request.session.pop("mysql_root_token", None)
            return ""
        password, _ = entry
        _session_mysql_credentials[token] = (
            password,
            now + _MYSQL_CREDENTIAL_TTL,
        )
        return password


def _mysql_page_response(
    request: Request,
    flash: dict | None = None,
    created_credentials: dict | None = None,
):
    user = require_auth(request)
    root_pass = get_root_pass(request)
    status = get_mysql_status()
    databases = list_databases(root_pass) if status["running"] else []
    users = list_users(root_pass) if status["running"] else []
    inventory_error = ""
    database_details = []
    database_grants = {}
    if status["running"]:
        try:
            database_details = list_database_details(root_pass)
            database_grants = list_database_grants(root_pass)
        except RuntimeError as exc:
            inventory_error = str(exc)
    response = templates.TemplateResponse(
        "mysql.html",
        {
            "request": request,
            "user": user,
            "active": "mysql",
            "status": status,
            "flash": flash,
            "databases": databases,
            "db_users": users,
            "managed_db_users": [
                account
                for account in users
                if account["user"].lower() not in PROTECTED_USERS
            ],
            "database_details": database_details,
            "database_grants": database_grants,
            "inventory_error": inventory_error,
            "phpmyadmin": check_phpmyadmin(),
            "has_root_pass": bool(root_pass),
            "created_credentials": created_credentials,
            "csrf_token": get_csrf_token(request),
        },
    )
    if created_credentials:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


@router.get("", response_class=HTMLResponse)
async def mysql_page(request: Request):
    """MySQL management page: status, databases, users."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    flash = request.session.pop("flash", None)
    return _mysql_page_response(request, flash)


@router.get("/config", response_class=HTMLResponse)
async def mysql_config_page(request: Request):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        config_path, config = get_mysql_config()
        config_path = str(Path(config_path).resolve(strict=True))
        error = ""
        config = read_config(Path(config_path))
    except (OSError, UnicodeError) as exc:
        config_path, config, error = "", "", str(exc)
    return templates.TemplateResponse(
        "mysql_config.html",
        {
            "request": request,
            "user": user,
            "active": "mysql",
            "config_path": config_path,
            "config": config,
            "error": error,
            "save_url": "/mysql/config",
            "flash": request.session.pop("flash", None),
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/config")
async def save_mysql_config(
    request: Request,
    config: str = Form(...),
    csrf_token: str = Form(...),
):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        request.session["flash"] = {"type": "error", "msg": "Run WebPanel as root to edit system configuration."}
        return RedirectResponse("/mysql/config", status_code=302)
    try:
        config_path, _ = get_mysql_config()
        edit_path = Path(config_path).resolve(strict=True)
        daemon = shutil.which("mysqld") or shutil.which("mariadbd")
        if not daemon:
            raise FileNotFoundError("MySQL server binary was not found; install MySQL before editing its configuration.")
        ok, message = save_config(
            edit_path,
            config,
            [[daemon, f"--defaults-file={edit_path}", "--validate-config"]],
            [["systemctl", "restart", "mysql"]],
        )
    except (OSError, UnicodeError) as exc:
        ok, message = False, str(exc)
    log_action(user, "MYSQL_CONFIG_SAVE", message, request.client.host if request.client else "unknown")
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/mysql/config", status_code=302)


@router.post("/set-root-pass")
async def set_root_pass(
    request: Request,
    root_pass: str = Form(""),
    csrf_token: str = Form(...),
):
    """Keep the MySQL root password in process memory, not the signed cookie."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    token = request.session.get("mysql_root_token")
    with _credential_lock:
        if token:
            _session_mysql_credentials.pop(token, None)
        request.session.pop("mysql_root_pass", None)
        if root_pass:
            token = secrets.token_urlsafe(32)
            _session_mysql_credentials[token] = (
                root_pass,
                time.monotonic() + _MYSQL_CREDENTIAL_TTL,
            )
            request.session["mysql_root_token"] = token
        else:
            request.session.pop("mysql_root_token", None)
    request.session["flash"] = {
        "type": "success",
        "msg": (
            "MySQL root password saved for this session."
            if root_pass
            else "MySQL root password cleared. WebPanel will use local socket authentication."
        ),
    }
    log_action(
        user,
        "MYSQL_ROOT_CREDENTIAL_SET" if root_pass else "MYSQL_ROOT_CREDENTIAL_CLEAR",
        "MySQL root credential stored in process memory" if root_pass else "Session credential cleared",
        request.client.host if request.client else "unknown",
    )
    return RedirectResponse("/mysql", status_code=302)


@router.post("/action")
async def mysql_control(
    request: Request,
    action: str = Form(...),
    csrf_token: str = Form(...),
):
    """Perform start/stop/restart on MySQL."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)

    if action not in ["start", "stop", "restart"]:
        return RedirectResponse("/mysql", status_code=302)

    ok, msg = mysql_action(action)
    ip = request.client.host if request.client else "unknown"
    log_action(user, f"MYSQL_{action.upper()}", msg, ip)
    request.session["flash"] = {
        "type": "success" if ok else "error",
        "msg": f"MySQL {action}: {'OK' if ok else msg}",
    }
    return RedirectResponse("/mysql", status_code=302)


@router.post("/create-db")
async def create_db(
    request: Request,
    db_name: str = Form(...),
    csrf_token: str = Form(...),
):
    """Create a new MySQL database."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)

    root_pass = get_root_pass(request)
    name = db_name.strip()
    ok, msg = create_database(name, root_pass)
    ip = request.client.host if request.client else "unknown"
    log_action(user, "MYSQL_CREATE_DB", name, ip)
    request.session["flash"] = {
        "type": "success" if ok else "error",
        "msg": f"Database '{name}' created successfully" if ok else msg,
    }
    return RedirectResponse("/mysql", status_code=302)


@router.post("/drop-db")
async def delete_db(
    request: Request,
    db_name: str = Form(...),
    csrf_token: str = Form(...),
):
    """Drop a MySQL database."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)

    root_pass = get_root_pass(request)
    name = db_name.strip()
    ok, msg = drop_database(name, root_pass)
    ip = request.client.host if request.client else "unknown"
    log_action(user, "MYSQL_DROP_DB", name, ip)
    request.session["flash"] = {
        "type": "success" if ok else "error",
        "msg": f"Database '{name}' dropped successfully" if ok else msg,
    }
    return RedirectResponse("/mysql", status_code=302)


@router.post("/create-user")
async def create_db_user(
    request: Request,
    db_username: str = Form(...),
    db_password: str = Form(...),
    db_name: str = Form(""),
    privilege_scope: str = Form("database"),
    db_host: str = Form("localhost"),
    csrf_token: str = Form(...),
):
    """Create a MySQL user and optionally grant access to a database."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)

    if privilege_scope not in {"database", "administrator"}:
        request.session["flash"] = {
            "type": "error",
            "msg": "Choose database-only or full administrator access.",
        }
        return RedirectResponse("/mysql#credentials", status_code=302)

    root_pass = get_root_pass(request)
    uname = db_username.strip()
    database = db_name.strip() if privilege_scope == "database" else ""
    global_admin = privilege_scope == "administrator"
    host = db_host.strip() or "localhost"
    ok, msg = create_user(
        uname,
        db_password,
        database,
        root_pass,
        global_admin=global_admin,
        host=host,
    )
    ip = request.client.host if request.client else "unknown"
    log_action(
        user,
        "MYSQL_CREATE_ADMIN" if global_admin else "MYSQL_CREATE_USER",
        uname,
        ip,
    )
    if ok:
        return _mysql_page_response(
            request,
            {"type": "success", "msg": f"User '{uname}' created successfully."},
            {
                "username": uname,
                "password": db_password,
                "database": (
                    "All databases (administrator)"
                    if global_admin
                    else database
                ),
                "global_admin": global_admin,
            },
        )
    return _mysql_page_response(
        request,
        {"type": "error", "msg": msg or "Could not create database user."},
    )


@router.post("/drop-user")
async def delete_db_user(
    request: Request,
    db_username: str = Form(...),
    db_host: str = Form("localhost"),
    csrf_token: str = Form(...),
):
    """Drop a MySQL user."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)

    root_pass = get_root_pass(request)
    uname = db_username.strip()
    ok, msg = drop_user(uname, db_host.strip(), root_pass)
    ip = request.client.host if request.client else "unknown"
    log_action(user, "MYSQL_DROP_USER", uname, ip)
    request.session["flash"] = {
        "type": "success" if ok else "error",
        "msg": f"User '{uname}' dropped successfully" if ok else msg,
    }
    return RedirectResponse("/mysql", status_code=302)


@router.post("/database-permission")
async def change_database_permission(
    request: Request,
    db_name: str = Form(...),
    db_username: str = Form(...),
    db_host: str = Form(...),
    permission: str = Form(...),
    csrf_token: str = Form(...),
):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    if permission not in {"grant", "revoke"}:
        ok, message = False, "Choose grant or revoke."
    else:
        ok, message = set_database_permission(
            db_name.strip(),
            db_username.strip(),
            db_host.strip(),
            permission == "grant",
            get_root_pass(request),
        )
    log_action(
        user,
        "MYSQL_PERMISSION_" + permission.upper() if permission in {"grant", "revoke"} else "MYSQL_PERMISSION_INVALID",
        f"{db_name}: {db_username}@{db_host}: {message}",
        request.client.host if request.client else "unknown",
    )
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/mysql#databases", status_code=302)


@router.post("/reset-user-password")
async def change_database_user_password(
    request: Request,
    db_username: str = Form(...),
    db_host: str = Form(...),
    csrf_token: str = Form(...),
):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    ok, message, password = reset_user_password(
        db_username.strip(),
        db_host.strip(),
        get_root_pass(request),
    )
    log_action(
        user,
        "MYSQL_USER_PASSWORD_RESET" if ok else "MYSQL_USER_PASSWORD_RESET_FAILED",
        f"{db_username}@{db_host}: {message}",
        request.client.host if request.client else "unknown",
    )
    if ok:
        response = _mysql_page_response(
            request,
            {"type": "success", "msg": "Database account password changed. Copy it now; it cannot be shown again."},
            {
                "username": db_username,
                "password": password,
                "database": "",
                "host": db_host,
                "reset": True,
            },
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response
    request.session["flash"] = {"type": "error", "msg": message}
    return RedirectResponse("/mysql#users", status_code=302)


@router.post("/backup/database/{db_name}")
async def download_database_backup(
    request: Request,
    db_name: str,
    csrf_token: str = Form(...),
):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    try:
        backup = create_database_backup(
            db_name,
            get_root_pass(request),
            DATABASE_BACKUP_DIR.parent,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        request.session["flash"] = {"type": "error", "msg": f"Database backup failed: {exc}"}
        log_action(
            user,
            "MYSQL_DATABASE_BACKUP_FAILED",
            f"{db_name}: {exc}",
            request.client.host if request.client else "unknown",
        )
        return RedirectResponse("/mysql#databases", status_code=302)
    log_action(
        user,
        "MYSQL_DATABASE_BACKUP",
        f"{db_name}: {backup.name}",
        request.client.host if request.client else "unknown",
    )
    return FileResponse(
        backup,
        media_type="application/sql",
        filename=backup.name,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/backup", response_class=HTMLResponse)
async def mysql_backup_page(request: Request):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    directory = backup_directory(DATABASE_BACKUP_DIR.parent)
    return templates.TemplateResponse(
        "mysql_backup.html",
        {
            "request": request,
            "user": user,
            "active": "mysql",
            "flash": request.session.pop("flash", None),
            "backups": list_backups(directory),
            "database_backups": list_database_backups(directory),
            "max_restore_size": MAX_RESTORE_SIZE // (1024 * 1024),
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/backup/create")
async def create_mysql_backup(
    request: Request,
    csrf_token: str = Form(...),
):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    try:
        backup = create_backup(get_root_pass(request), DATABASE_BACKUP_DIR.parent)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        request.session["flash"] = {"type": "error", "msg": f"Backup failed: {exc}"}
        log_action(user, "MYSQL_BACKUP_FAILED", str(exc), request.client.host if request.client else "unknown")
        return RedirectResponse("/mysql/backup", status_code=302)
    log_action(user, "MYSQL_BACKUP", backup.name, request.client.host if request.client else "unknown")
    return FileResponse(
        backup,
        media_type="application/sql",
        filename=backup.name,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/backup/{filename}")
async def download_mysql_backup(request: Request, filename: str):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        backup = find_backup(backup_directory(DATABASE_BACKUP_DIR.parent), filename)
    except (FileNotFoundError, OSError):
        return HTMLResponse("Backup file not found.", status_code=404)
    return FileResponse(
        backup,
        media_type="application/sql",
        filename=backup.name,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/backup/database/file/{filename}")
async def download_saved_database_backup(request: Request, filename: str):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        backup = find_database_backup(
            backup_directory(DATABASE_BACKUP_DIR.parent),
            filename,
        )
    except (FileNotFoundError, OSError):
        return HTMLResponse("Database backup file not found.", status_code=404)
    log_action(
        user,
        "MYSQL_DATABASE_BACKUP_DOWNLOAD",
        backup.name,
        request.client.host if request.client else "unknown",
    )
    return FileResponse(
        backup,
        media_type="application/sql",
        filename=backup.name,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/restore")
async def restore_mysql_backup(
    request: Request,
    backup_file: UploadFile = File(...),
    csrf_token: str = Form(...),
):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    if not backup_file.filename or not backup_file.filename.lower().endswith(".sql"):
        request.session["flash"] = {"type": "error", "msg": "Choose a .sql backup file."}
        return RedirectResponse("/mysql/backup", status_code=302)
    if backup_file.size is not None and backup_file.size > MAX_RESTORE_SIZE:
        request.session["flash"] = {"type": "error", "msg": f"Restore files are limited to {MAX_RESTORE_SIZE // (1024 * 1024)} MiB."}
        return RedirectResponse("/mysql/backup", status_code=302)
    try:
        backup_file.file.seek(0, os.SEEK_END)
        actual_size = backup_file.file.tell()
        backup_file.file.seek(0)
        if actual_size > MAX_RESTORE_SIZE:
            request.session["flash"] = {"type": "error", "msg": f"Restore files are limited to {MAX_RESTORE_SIZE // (1024 * 1024)} MiB."}
            return RedirectResponse("/mysql/backup", status_code=302)
        ok, message = restore_backup(get_root_pass(request), backup_file.file)
    except OSError as exc:
        ok, message = False, str(exc)
    finally:
        await backup_file.close()
    log_action(
        user,
        "MYSQL_RESTORE" if ok else "MYSQL_RESTORE_FAILED",
        backup_file.filename,
        request.client.host if request.client else "unknown",
    )
    request.session["flash"] = {
        "type": "success" if ok else "error",
        "msg": "Database restore completed." if ok else f"Restore failed: {message}",
    }
    return RedirectResponse("/mysql/backup", status_code=302)
