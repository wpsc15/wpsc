import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import log_action
from services.docker_manager import (
    control_container,
    control_docker_service,
    list_containers,
    read_container_logs,
)
from services.web_security import get_csrf_token, require_valid_csrf
from services.stack_installer import get_docker_options, get_install_status

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/docker")


def _log_action(request: Request, user: str, action: str, detail: str) -> None:
    log_action(
        user,
        action,
        detail,
        request.client.host if request.client else "unknown",
    )


@router.get("", response_class=HTMLResponse)
async def docker_page(request: Request, logs: str = ""):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        containers = list_containers()
        error = ""
    except (RuntimeError, ValueError) as exc:
        containers, error = [], str(exc)

    container_logs = ""
    if logs and not error:
        try:
            container_logs = read_container_logs(logs)
        except (RuntimeError, ValueError) as exc:
            error = str(exc)

    return templates.TemplateResponse(
        "docker.html",
        {
            "request": request,
            "user": user,
            "active": "docker",
            "flash": request.session.pop("flash", None),
            "containers": containers,
            "error": error,
            "selected_logs": logs,
            "container_logs": container_logs,
            "csrf_token": get_csrf_token(request),
            "docker_options": await asyncio.to_thread(get_docker_options),
            "docker_installed": bool(shutil.which("docker")),
            "install_job": get_install_status(user),
        },
    )


@router.post("/action")
async def docker_control(
    request: Request,
    container_id: str = Form(...),
    action: str = Form(...),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    ok, message = control_container(container_id, action)
    event = f"DOCKER_{action.upper()}" if action in {"start", "stop", "restart"} else "DOCKER_INVALID_ACTION"
    _log_action(request, user, event, f"{container_id[:64]}: {message}")
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/docker", status_code=302)


@router.post("/service")
async def docker_service_control(
    request: Request,
    action: str = Form(...),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    ok, message = control_docker_service(action)
    event = (
        f"DOCKER_SERVICE_{action.upper()}"
        if action in {"start", "restart", "stop"}
        else "DOCKER_SERVICE_INVALID_ACTION"
    )
    _log_action(request, user, event, message)
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/docker", status_code=302)
