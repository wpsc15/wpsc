from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from services.system import get_system_info, bytes_to_human
from services.apache import get_apache_status
from services.mysql_svc import get_mysql_status, list_databases
from services.php_svc import get_current_php_version, get_installed_php_versions
from services.system import service_status
from services.websites import list_websites

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter()


def require_auth(request: Request):
    """Return username from session or None."""
    if not request.session.get("user"):
        return None
    return request.session["user"]


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard: system info, service statuses, quick actions."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    flash = request.session.pop("flash", None)
    sys_info = get_system_info()
    apache = get_apache_status()
    mysql = get_mysql_status()
    php_ver = get_current_php_version()
    php_versions = get_installed_php_versions()
    websites = list_websites()
    database_count = len(list_databases()) if mysql["running"] else 0
    php_fpm_running = bool(
        php_versions and service_status(f"php{php_versions[0]}-fpm") == "active"
    )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "active": "dashboard",
            "flash": flash,
            "sys": sys_info,
            "apache": apache,
            "mysql": mysql,
            "php_version": php_ver,
            "php_versions": php_versions,
            "website_count": len(websites),
            "database_count": database_count,
            "apache_running": apache["running"],
            "mysql_running": mysql["running"],
            "php_fpm_running": php_fpm_running,
            "bytes_to_human": bytes_to_human,
        },
    )
