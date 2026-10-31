from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import log_action
from services.server_logs import available_log_services, clear_server_journal, read_service_log
from services.web_security import get_csrf_token, require_valid_csrf

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/logs")


@router.get("", response_class=HTMLResponse)
async def logs_page(request: Request, unit: str = ""):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)

    try:
        services = available_log_services()
        error = ""
    except RuntimeError as exc:
        services = []
        error = str(exc)

    log_output = ""
    if unit and not error:
        try:
            log_output = read_service_log(unit)
        except (RuntimeError, ValueError) as exc:
            error = str(exc)

    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "user": user,
            "active": "logs",
            "flash": request.session.pop("flash", None),
            "services": services,
            "selected_unit": unit,
            "log_output": log_output,
            "error": error,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/clear")
async def clear_logs(
    request: Request,
    unit: str = Form(...),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)

    try:
        clear_server_journal(unit)
        ok, message = True, (
            "Existing journal history for all services and dedicated log files for the "
            f"selected service ({unit}) were deleted. Shared syslog files were left unchanged."
        )
        action = "SERVER_JOURNAL_CLEAR"
    except (RuntimeError, ValueError) as exc:
        ok, message = False, str(exc)
        action = "SERVER_JOURNAL_CLEAR_FAILED"
    log_action(
        user,
        action,
        message,
        request.client.host if request.client else "unknown",
    )
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/logs", status_code=302)
