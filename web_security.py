import secrets

from fastapi import HTTPException, Request


def get_csrf_token(request: Request) -> str:
    token = request.session.get("_csrf_token")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session["_csrf_token"] = token
    return token


def require_valid_csrf(request: Request, token: str) -> None:
    expected = request.session.get("_csrf_token")
    if not isinstance(expected, str) or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
