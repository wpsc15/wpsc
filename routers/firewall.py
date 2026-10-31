from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import log_action
from services.firewall import (
    change_firewall_rule,
    get_firewall_status,
    remove_firewall_rule,
    set_firewall_state,
)
from services.web_security import get_csrf_token, require_valid_csrf

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/firewall")


def _audit(request: Request, user: str, action: str, message: str) -> None:
    log_action(
        user,
        action,
        message,
        request.client.host if request.client else "unknown",
    )


@router.get("", response_class=HTMLResponse)
async def firewall_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        "firewall.html",
        {
            "request": request,
            "user": user,
            "active": "firewall",
            "flash": request.session.pop("flash", None),
            "firewall": get_firewall_status(),
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/state")
async def firewall_state(
    request: Request,
    action: str = Form(...),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    ok, message = set_firewall_state(action)
    _audit(request, user, f"FIREWALL_{action.upper()}", message)
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/firewall", status_code=302)


@router.post("/rule")
async def firewall_rule(
    request: Request,
    action: str = Form(...),
    port: str = Form(...),
    protocol: str = Form(...),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    ok, message = change_firewall_rule(action, port, protocol)
    _audit(request, user, f"FIREWALL_RULE_{action.upper()}", message)
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/firewall", status_code=302)


@router.post("/rule/remove")
async def firewall_rule_remove(
    request: Request,
    action: str = Form(...),
    port: str = Form(...),
    protocol: str = Form(...),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    ok, message = remove_firewall_rule(action, port, protocol)
    _audit(request, user, "FIREWALL_RULE_REMOVE", message)
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/firewall", status_code=302)
