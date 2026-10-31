from fastapi import Request
from fastapi.responses import RedirectResponse


def get_current_user(request: Request):
    """Return the session username or None."""
    user = request.session.get("user")
    if not user:
        return None
    return user


class RequireAuth:
    """Dependency class that returns the current user or None."""

    def __call__(self, request: Request):
        user = request.session.get("user")
        if not user:
            return None
        return user

