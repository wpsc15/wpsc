import hashlib
import logging
import re
import secrets
import smtplib
import sqlite3
from urllib.parse import quote
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from database import (
    clear_password_reset_token,
    consume_password_reset_token,
    create_password_reset_token,
    get_user,
    hash_password,
    log_action,
    normalize_email,
    verify_password,
)
from services.password_reset import build_reset_url, send_password_reset_email
from services.web_security import get_csrf_token, require_valid_csrf

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Show login page. Redirect to dashboard if already authenticated."""
    if request.session.get("user"):
        return RedirectResponse("/dashboard", status_code=302)
    error = request.session.pop("flash_error", None)
    notice = request.session.pop("flash_success", None)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": error, "notice": notice}
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Process login form. Set session on success."""
    user = get_user(username)
    ip = request.client.host if request.client else "unknown"
    if user and verify_password(password, user["password"]):
        request.session["user"] = username
        log_action(username, "LOGIN", "Successful login", ip)
        return RedirectResponse("/dashboard", status_code=302)
    log_action(username, "LOGIN_FAIL", "Failed login attempt", ip)
    request.session["flash_error"] = "Invalid username or password"
    return RedirectResponse("/login", status_code=302)


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        "forgot_password.html",
        {
            "request": request,
            "csrf_token": get_csrf_token(request),
            "notice": request.session.pop("reset_notice", None),
            "error": request.session.pop("reset_error", None),
        },
    )


@router.post("/forgot-password")
async def request_password_reset(
    request: Request,
    email: str = Form(...),
    csrf_token: str = Form(...),
):
    require_valid_csrf(request, csrf_token)
    generic_notice = "If an account uses that email, a password reset link will be sent shortly."
    token_hash = None

    try:
        normalized_email = normalize_email(email)
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        account = create_password_reset_token(normalized_email, token_hash)
        if account:
            reset_url = build_reset_url(token)
            send_password_reset_email(account["email"], reset_url)
    except (ValueError, smtplib.SMTPException, OSError, sqlite3.Error):
        logger.exception("Could not process a password reset request")
        if token_hash:
            try:
                clear_password_reset_token(token_hash)
            except sqlite3.Error:
                logger.exception("Could not clear an undelivered password reset token")

    request.session["reset_notice"] = generic_notice
    return RedirectResponse("/forgot-password", status_code=302)


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = ""):
    return templates.TemplateResponse(
        "reset_password.html",
        {
            "request": request,
            "token": token,
            "csrf_token": get_csrf_token(request),
            "error": request.session.pop("reset_error", None),
        },
    )


@router.post("/reset-password")
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...),
):
    require_valid_csrf(request, csrf_token)
    if len(token) != 43 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        request.session["reset_error"] = "This reset link is invalid or has expired. Request a new link."
        return RedirectResponse("/forgot-password", status_code=302)
    if new_password != confirm_password:
        request.session["reset_error"] = "The new passwords do not match."
        return RedirectResponse(f"/reset-password?token={quote(token, safe='')}", status_code=302)
    if len(new_password.encode("utf-8")) < 12:
        request.session["reset_error"] = "Choose a password with at least 12 UTF-8 bytes."
        return RedirectResponse(f"/reset-password?token={quote(token, safe='')}", status_code=302)
    if len(new_password.encode("utf-8")) > 72:
        request.session["reset_error"] = "The new password must be no more than 72 UTF-8 bytes."
        return RedirectResponse(f"/reset-password?token={quote(token, safe='')}", status_code=302)

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    try:
        username = consume_password_reset_token(token_hash, hash_password(new_password))
    except sqlite3.Error:
        logger.exception("Could not complete password reset")
        request.session["reset_error"] = "Could not reset the password. Check the server logs."
        return RedirectResponse(f"/reset-password?token={quote(token, safe='')}", status_code=302)

    if not username:
        request.session["reset_error"] = "This reset link is invalid or has expired. Request a new link."
        return RedirectResponse("/forgot-password", status_code=302)

    log_action(
        username,
        "ACCOUNT_PASSWORD_RESET",
        "Password reset using email link",
        request.client.host if request.client else "unknown",
    )
    request.session["flash_success"] = "Password reset successfully. Sign in with your new password."
    return RedirectResponse("/login", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    user = request.session.get("user", "unknown")
    log_action(user, "LOGOUT", "", request.client.host if request.client else "unknown")
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@router.get("/")
async def root(request: Request):
    """Root redirect."""
    if request.session.get("user"):
        return RedirectResponse("/dashboard", status_code=302)
    return RedirectResponse("/login", status_code=302)
