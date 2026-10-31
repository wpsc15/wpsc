import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import log_action
from services.stack_installer import (
    get_docker_options,
    get_app_catalog,
    get_install_status,
    get_stack_options,
    start_install,
    validate_install_request,
)
from services.web_security import get_csrf_token, require_valid_csrf

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/stack")


@router.get("", response_class=HTMLResponse)
async def stack_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    options = await asyncio.to_thread(get_stack_options)
    docker_options = await asyncio.to_thread(get_docker_options)
    return templates.TemplateResponse(
        "stack.html",
        {
            "request": request,
            "user": user,
            "active": "stack",
            "flash": request.session.pop("flash", None),
            "options": options,
            "docker_options": docker_options,
            "apps": get_app_catalog(options, docker_options),
            "job": get_install_status(user),
            "csrf_token": get_csrf_token(request),
        },
    )


@router.get("/terminal", response_class=HTMLResponse)
async def installer_terminal(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        "installer_terminal.html",
        {"request": request, "user": user},
    )


@router.post("/install")
async def install(
    request: Request,
    component: str = Form(...),
    version: str = Form(...),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "Authentication required."}, status_code=401)
    require_valid_csrf(request, csrf_token)

    error = await asyncio.to_thread(validate_install_request, component, version)
    if error:
        return JSONResponse({"error": error}, status_code=400)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        return JSONResponse(
            {"error": "Run WebPanel as root to install system packages."},
            status_code=403,
        )
    started, result = start_install(component, version, user)
    if not started:
        return JSONResponse({"error": result}, status_code=409)

    log_action(
        user,
        f"{component.upper()}_INSTALL",
        f"{component} {version}",
        request.client.host if request.client else "unknown",
    )
    return JSONResponse(
        {"message": f"{component.upper()} installation started.", "job_id": result},
        status_code=202,
    )


@router.get("/status")
async def install_status(request: Request):
    user = request.session.get("user")
    if not user:
        return JSONResponse({"error": "Authentication required."}, status_code=401)
    job_id = request.query_params.get("job_id", "")
    return JSONResponse(get_install_status(user, job_id))
