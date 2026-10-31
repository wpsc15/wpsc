from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import os
from services.apache import (
    get_apache_status,
    apache_action,
    get_error_log,
    get_access_log,
    get_apache_config_path,
    get_apache_vhost,
)
from services.config_editor import read_config, save_config
from services.system import service_status
from services.domains import validate_domain
from database import log_action

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/apache")


def require_auth(request: Request):
    return request.session.get("user")


@router.get("", response_class=HTMLResponse)
async def apache_page(request: Request, log: str = ""):
    """Apache management page with optional log display."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    flash = request.session.pop("flash", None)
    status = get_apache_status()
    error_log = ""
    access_log = ""
    if log == "error":
        error_log = get_error_log(100)
    elif log == "access":
        access_log = get_access_log(100)

    return templates.TemplateResponse(
        "apache.html",
        {
            "request": request,
            "user": user,
            "active": "apache",
            "status": status,
            "flash": flash,
            "error_log": error_log,
            "access_log": access_log,
            "log": log,
        },
    )


@router.post("/action")
async def apache_control(request: Request, action: str = Form(...)):
    """Perform a start/stop/restart/reload action on Apache."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    if action not in ["start", "stop", "restart", "reload"]:
        request.session["flash"] = {"type": "error", "msg": "Invalid action"}
        return RedirectResponse("/apache", status_code=302)

    ok, msg = apache_action(action)
    ip = request.client.host if request.client else "unknown"
    log_action(user, f"APACHE_{action.upper()}", msg, ip)
    request.session["flash"] = {
        "type": "success" if ok else "error",
        "msg": f"Apache {action}: {'OK' if ok else msg}",
    }
    return RedirectResponse("/apache", status_code=302)


@router.get("/config", response_class=HTMLResponse)
async def apache_config(request: Request):
    """Display and safely edit the main Apache configuration file."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    try:
        config_path = get_apache_config_path()
        config = read_config(config_path)
        error = ""
    except (OSError, UnicodeError) as exc:
        config_path, config, error = "", "", str(exc)
    return templates.TemplateResponse(
        "apache_config.html",
        {
            "request": request,
            "user": user,
            "active": "apache",
            "config": config,
            "config_path": str(config_path) or "Apache configuration",
            "config_title": "Apache Configuration File",
            "error": error,
            "save_url": "/apache/config",
            "flash": request.session.pop("flash", None),
        },
    )


@router.post("/config")
async def save_apache_config(request: Request, config: str = Form(...)):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        request.session["flash"] = {"type": "error", "msg": "Run WebPanel as root to edit system configuration."}
        return RedirectResponse("/apache/config", status_code=302)
    try:
        path = get_apache_config_path()
        if path.as_posix().startswith("/etc/httpd/"):
            validator, service = ["apachectl", "configtest"], "httpd"
        else:
            validator, service = ["apache2ctl", "configtest"], "apache2"
        reload_commands = [["systemctl", "reload", service]] if service_status(service) == "active" else []
        ok, message = save_config(
            path,
            config,
            [validator],
            reload_commands,
        )
        if ok and not reload_commands:
            message += " Apache is not active; changes will apply when it starts."
    except (OSError, UnicodeError) as exc:
        ok, message = False, str(exc)
    log_action(user, "APACHE_CONFIG_SAVE", message, request.client.host if request.client else "unknown")
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/apache/config", status_code=302)


@router.get("/vhost/{domain}", response_class=HTMLResponse)
async def apache_vhost_config(request: Request, domain: str):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        config_path, config = get_apache_vhost(domain)
    except (ValueError, OSError) as exc:
        request.session["flash"] = {"type": "error", "msg": str(exc)}
        return RedirectResponse("/websites", status_code=302)
    return templates.TemplateResponse(
        "apache_config.html",
        {
            "request": request,
            "user": user,
            "active": "apache",
            "config": config,
            "config_path": config_path,
            "config_title": f"Virtual host: {domain}",
            "error": "",
            "save_url": f"/apache/vhost/{domain}",
            "flash": request.session.pop("flash", None),
        },
    )


@router.post("/vhost/{domain}")
async def save_apache_vhost(request: Request, domain: str, config: str = Form(...)):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        domain = validate_domain(domain)
    except ValueError as exc:
        request.session["flash"] = {"type": "error", "msg": str(exc)}
        return RedirectResponse("/websites", status_code=302)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        request.session["flash"] = {"type": "error", "msg": "Run WebPanel as root to edit system configuration."}
        return RedirectResponse(f"/apache/vhost/{domain}", status_code=302)
    try:
        path_string, _ = get_apache_vhost(domain)
        reload_commands = [["systemctl", "reload", "apache2"]] if service_status("apache2") == "active" else []
        ok, message = save_config(
            Path(path_string),
            config,
            [["apache2ctl", "configtest"]],
            reload_commands,
        )
        if ok and not reload_commands:
            message += " Apache is not active; changes will apply when it starts."
    except (OSError, UnicodeError, ValueError) as exc:
        ok, message = False, str(exc)
    log_action(user, "APACHE_VHOST_SAVE", message, request.client.host if request.client else "unknown")
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse(f"/apache/vhost/{domain}", status_code=302)
