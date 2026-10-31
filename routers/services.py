from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import log_action
from services.system_services import list_managed_services, manage_service
from services.web_security import get_csrf_token, require_valid_csrf

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/services")


@router.get("", response_class=HTMLResponse)
async def services_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)

    try:
        services = list_managed_services()
        error = ""
    except RuntimeError as exc:
        services = []
        error = str(exc)
    return templates.TemplateResponse(
        "services.html",
        {
            "request": request,
            "user": user,
            "active": "services",
            "flash": request.session.pop("flash", None),
            "services": services,
            "categories": sorted({service["category"] for service in services}),
            "error": error,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/action")
async def service_control(
    request: Request,
    unit: str = Form(...),
    action: str = Form(...),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)

    ok, message = manage_service(unit, action)
    log_action(
        user,
        f"SERVICE_{action.upper()}" if action in {"start", "stop", "restart"} else "SERVICE_INVALID_ACTION",
        f"{unit}: {message}",
        request.client.host if request.client else "unknown",
    )
    request.session["flash"] = {
        "type": "success" if ok else "error",
        "msg": message,
    }
    return RedirectResponse("/services", status_code=302)
