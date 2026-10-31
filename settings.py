import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import (
    create_user_account,
    get_db,
    get_user,
    hash_password,
    list_users,
    log_action,
    set_user_email,
    verify_password,
)
from services.web_security import get_csrf_token, require_valid_csrf

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/settings")


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "account": get_user(user),
            "active": "settings",
            "flash": request.session.pop("flash", None),
            "csrf_token": get_csrf_token(request),
        },
    )


@router.get("/accounts", response_class=HTMLResponse)
async def manage_accounts(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)

    account = get_user(user)
    if not account or account.get("role") != "admin":
        request.session["flash"] = {
            "type": "error",
            "msg": "Only administrators can create and manage user accounts.",
        }
        return RedirectResponse("/settings", status_code=302)

    return templates.TemplateResponse(
        "settings_accounts.html",
        {
            "request": request,
            "user": user,
            "active": "settings",
            "flash": request.session.pop("flash", None),
            "accounts": list_users(),
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/accounts/create")
async def create_account(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    allowed_domains: str = Form(""),
    role: str = Form("restricted"),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)

    account = get_user(user)
    if not account or account.get("role") != "admin":
        request.session["flash"] = {
            "type": "error",
            "msg": "Only administrators can create user accounts.",
        }
        return RedirectResponse("/settings", status_code=302)

    require_valid_csrf(request, csrf_token)

    try:
        if password != confirm_password:
            raise ValueError("Passwords do not match.")
        if len(password.encode("utf-8")) < 12:
            raise ValueError("Password must be at least 12 UTF-8 bytes long.")
        if role not in {"admin", "restricted"}:
            role = "restricted"

        created = create_user_account(
            username=username,
            password=password,
            role=role,
            allowed_domains=allowed_domains,
            email=email,
        )
        ok, message = True, f"Account '{created['username']}' created successfully."
    except ValueError as exc:
        ok, message = False, str(exc)
    except sqlite3.Error:
        logger.exception("Could not create user account")
        ok, message = False, "Could not create the user account. Check the server logs."

    if ok:
        log_action(
            user,
            "USER_CREATE",
            f"Created account {username}",
            request.client.host if request.client else "unknown",
        )
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/settings/accounts", status_code=302)


@router.post("/email")
async def update_account_email(
    request: Request,
    email: str = Form(...),
    current_password: str = Form(...),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)

    account = get_user(user)
    if not account or not verify_password(current_password, account["password"]):
        ok, message = False, "Current password is incorrect."
    else:
        try:
            set_user_email(user, email)
            ok, message = True, "Recovery email updated."
        except ValueError as exc:
            ok, message = False, str(exc)
        except sqlite3.Error:
            logger.exception("Could not update recovery email for user %s", user)
            ok, message = False, "Could not update the recovery email. Check the server logs."

    if ok:
        log_action(
            user,
            "ACCOUNT_EMAIL_CHANGE",
            "Recovery email updated",
            request.client.host if request.client else "unknown",
        )
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/settings", status_code=302)


@router.post("/password")
async def update_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)

    record = get_user(user)
    if not record or not verify_password(current_password, record["password"]):
        ok, message = False, "Current password is incorrect."
    elif new_password != confirm_password:
        ok, message = False, "New passwords do not match."
    elif len(new_password.encode("utf-8")) < 12:
        ok, message = False, "Choose a new password with at least 12 UTF-8 bytes."
    elif len(new_password.encode("utf-8")) > 72:
        ok, message = False, "The new password must be no more than 72 UTF-8 bytes."
    elif new_password == current_password:
        ok, message = False, "Choose a password different from your current password."
    else:
        connection = get_db()
        try:
            connection.execute(
                "UPDATE users SET password = ? WHERE username = ?",
                (hash_password(new_password), user),
            )
            connection.commit()
            ok, message = True, "Password updated. Use the new password next time you sign in."
        except sqlite3.Error:
            logger.exception("Could not update password for user %s", user)
            ok, message = False, "Could not update the password. Check the server logs."
        finally:
            connection.close()

    if ok:
        log_action(
            user,
            "ACCOUNT_PASSWORD_CHANGE",
            "Password updated",
            request.client.host if request.client else "unknown",
        )
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/settings", status_code=302)
