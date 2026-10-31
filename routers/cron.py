from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import log_action
from services.cron_jobs import add_managed_job, list_managed_jobs, remove_managed_job
from services.web_security import get_csrf_token, require_valid_csrf

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/cron")


@router.get("", response_class=HTMLResponse)
async def cron_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        jobs = list_managed_jobs()
        error = ""
    except (OSError, RuntimeError, PermissionError) as exc:
        jobs, error = [], str(exc)
    return templates.TemplateResponse(
        "cron.html",
        {
            "request": request,
            "user": user,
            "active": "cron",
            "flash": request.session.pop("flash", None),
            "jobs": jobs,
            "error": error,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/add")
async def cron_add(
    request: Request,
    schedule: str = Form(...),
    command: str = Form(...),
    csrf_token: str = Form(...),
    custom_schedule: str = Form(""),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    try:
        if schedule == "custom":
            schedule = custom_schedule
        job_id = add_managed_job(schedule, command)
        ok, message = True, f"Scheduled job {job_id[:8]} was added."
        action = "CRON_JOB_ADD"
    except (OSError, RuntimeError, ValueError, PermissionError) as exc:
        ok, message = False, str(exc)
        action = "CRON_JOB_ADD_FAILED"
    log_action(
        user,
        action,
        message,
        request.client.host if request.client else "unknown",
    )
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/cron", status_code=302)


@router.post("/delete")
async def cron_delete(
    request: Request,
    job_id: str = Form(...),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    try:
        remove_managed_job(job_id)
        ok, message = True, f"Scheduled job {job_id[:8]} was removed."
        action = "CRON_JOB_REMOVE"
    except (OSError, RuntimeError, ValueError, PermissionError) as exc:
        ok, message = False, str(exc)
        action = "CRON_JOB_REMOVE_FAILED"
    log_action(
        user,
        action,
        message,
        request.client.host if request.client else "unknown",
    )
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/cron", status_code=302)
