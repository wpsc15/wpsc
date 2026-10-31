import sqlite3
from pathlib import Path

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import get_db
from services.web_security import get_csrf_token, require_valid_csrf

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/audit")
PAGE_SIZE = 50


@router.get("", response_class=HTMLResponse)
async def audit_page(
    request: Request,
    page: int = Query(default=1, ge=1, le=100000),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)

    connection = get_db()
    try:
        total = connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        rows = connection.execute(
            """SELECT id, user, action, detail, ip, created_at
               FROM audit_log
               ORDER BY id DESC
               LIMIT ? OFFSET ?""",
            (PAGE_SIZE, (page - 1) * PAGE_SIZE),
        ).fetchall()
    except sqlite3.Error as exc:
        return templates.TemplateResponse(
            "audit.html",
            {
                "request": request,
                "user": user,
                "active": "audit",
                "flash": request.session.pop("flash", None),
                "entries": [],
                "page": page,
                "pages": 1,
                "error": f"Could not read the audit log: {exc}",
                "csrf_token": get_csrf_token(request),
            },
            status_code=500,
        )
    finally:
        connection.close()

    return templates.TemplateResponse(
        "audit.html",
        {
            "request": request,
            "user": user,
            "active": "audit",
            "flash": request.session.pop("flash", None),
            "entries": [dict(row) for row in rows],
            "page": page,
            "pages": pages,
            "error": "",
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/clear")
async def clear_audit_log(
    request: Request,
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)

    connection = get_db()
    try:
        connection.execute("DELETE FROM audit_log")
        connection.commit()
    except sqlite3.Error as exc:
        request.session["flash"] = {
            "type": "error",
            "msg": f"Could not delete the activity log: {exc}",
        }
    else:
        request.session["flash"] = {
            "type": "success",
            "msg": "Activity log deleted.",
        }
    finally:
        connection.close()

    return RedirectResponse("/audit", status_code=302)
