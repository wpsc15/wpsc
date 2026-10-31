import asyncio
import os
import sqlite3
import tarfile
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import log_action
from routers.mysql_router import get_root_pass
from services.web_security import get_csrf_token, require_valid_csrf
from services.websites import discover_wordpress_sites, list_websites
from services.wordpress import (
    WORDPRESS_LOCALES,
    available_php_fpm_versions,
    create_wordpress_backup,
    find_wordpress_backup,
    install_wordpress,
    list_wordpress_backups,
)

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/wp-toolkit")


@router.get("", response_class=HTMLResponse)
async def wordpress_toolkit_page(request: Request, view: str = "local"):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    websites = list_websites()
    wordpress_sites = discover_wordpress_sites(websites)
    if view not in {"local", "remote", "statistics"}:
        view = "local"
    for site in wordpress_sites:
        site["backups"] = list_wordpress_backups(site["domain"])
    return templates.TemplateResponse(
        "wp_toolkit.html",
        {
            "request": request,
            "user": user,
            "active": "wp-toolkit",
            "flash": request.session.pop("flash", None),
            "csrf_token": get_csrf_token(request),
            "wordpress_sites": wordpress_sites,
            "view": view,
            "can_install": getattr(os, "geteuid", lambda: -1)() == 0,
            "php_versions": available_php_fpm_versions(),
            "languages": WORDPRESS_LOCALES,
        },
    )


@router.post("/install")
async def install_wordpress_site(
    request: Request,
    domain: str = Form(...),
    site_title: str = Form(...),
    admin_email: str = Form(...),
    php_version: str = Form(""),
    wordpress_version: str = Form("latest"),
    language: str = Form("en_US"),
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    try:
        result = await asyncio.to_thread(
            install_wordpress,
            domain,
            get_root_pass(request),
            site_title,
            admin_email,
            php_version,
            wordpress_version,
            language,
        )
        log_action(
            user,
            "WORDPRESS_INSTALL",
            f"Installed WordPress site {result['domain']} with PHP {php_version or 'system default'}",
            request.client.host if request.client else "unknown",
        )
        wordpress_sites = discover_wordpress_sites(list_websites())
        for site in wordpress_sites:
            site["backups"] = list_wordpress_backups(site["domain"])
        response = templates.TemplateResponse(
            "wp_toolkit.html",
            {
                "request": request,
                "user": user,
                "active": "wp-toolkit",
                "flash": None,
                "csrf_token": get_csrf_token(request),
                "wordpress_sites": wordpress_sites,
                "view": "local",
                "can_install": True,
                "php_versions": available_php_fpm_versions(),
                "languages": WORDPRESS_LOCALES,
                "setup_credentials": result,
            },
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response
    except (OSError, RuntimeError, ValueError, PermissionError, sqlite3.Error, tarfile.TarError) as exc:
        request.session["flash"] = {"type": "error", "msg": str(exc)}
        log_action(
            user,
            "WORDPRESS_INSTALL_FAIL",
            str(exc),
            request.client.host if request.client else "unknown",
        )
    return RedirectResponse("/wp-toolkit", status_code=302)


@router.post("/backup/{domain}")
async def backup_wordpress_site(
    request: Request,
    domain: str,
    csrf_token: str = Form(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    try:
        websites = list_websites()
        sites = discover_wordpress_sites(websites)
        site = next((item for item in sites if item["domain"] == domain), None)
        if site is None:
            raise ValueError("WordPress installation was not found.")
        backup = await asyncio.to_thread(create_wordpress_backup, site)
        request.session["flash"] = {
            "type": "success",
            "msg": f"Files and database backed up: {backup.name}",
        }
        log_action(
            user,
            "WORDPRESS_BACKUP",
            f"{domain}: {backup.name}",
            request.client.host if request.client else "unknown",
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error, tarfile.TarError) as exc:
        request.session["flash"] = {"type": "error", "msg": str(exc)}
        log_action(
            user,
            "WORDPRESS_BACKUP_FAIL",
            f"{domain}: {exc}",
            request.client.host if request.client else "unknown",
        )
    return RedirectResponse("/wp-toolkit", status_code=302)


@router.get("/backup/download/{filename}")
async def download_wordpress_backup(request: Request, filename: str):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=302)
    try:
        backup = find_wordpress_backup(filename)
    except (OSError, ValueError) as exc:
        request.session["flash"] = {"type": "error", "msg": str(exc)}
        return RedirectResponse("/wp-toolkit", status_code=302)
    return FileResponse(
        backup,
        media_type="application/gzip",
        filename=backup.name,
        headers={"Cache-Control": "no-store"},
    )
