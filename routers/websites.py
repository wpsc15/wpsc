import asyncio
import os
import sqlite3
import subprocess
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from database import get_user, log_action, user_has_domain_access
from services.domains import validate_domain
from services.apache import get_apache_vhost
from services.letsencrypt import issue_apache_certificate
from services.websites import (
    add_website,
    check_all_websites,
    create_apache_vhost,
    delete_website,
    list_websites,
    set_website_vhost,
)
from services.system import service_status
from services.web_security import get_csrf_token, require_valid_csrf

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/websites")


@router.get("", response_class=HTMLResponse)
async def websites_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)

    user_record = get_user(user)
    websites = list_websites()
    if user_record and user_record.get("role") != "admin":
        websites = [
            site
            for site in websites
            if user_has_domain_access(
                user,
                site.get("vhost_domain") or urlsplit(site.get("url") or "").hostname or "",
            )
        ]

    return templates.TemplateResponse(
        "websites.html",
        {
            "request": request,
            "user": user,
            "active": "websites",
            "flash": request.session.pop("flash", None),
            "websites": websites,
            "csrf_token": get_csrf_token(request),
            "apache_status": service_status("apache2"),
        },
    )


@router.post("/add")
async def add_monitored_website(
    request: Request,
    domain: str = Form(...),
    name: str = Form(""),
    scheme: str = Form("http"),
    create_vhost: bool = Form(False),
    csrf_token: str = Form(""),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    try:
        domain = validate_domain(domain)
        user_record = get_user(user)
        if user_record and user_record.get("role") != "admin" and not user_has_domain_access(user, domain):
            raise ValueError("This account is restricted to the domain assigned to it.")
        if scheme not in {"http", "https"}:
            raise ValueError("Select HTTP or HTTPS.")
        if create_vhost and scheme != "http":
            raise ValueError(
                "The generated Apache virtual host uses HTTP. Choose HTTP here and configure TLS separately."
            )
        display_name = name.strip() or domain
        website_id = add_website(display_name, f"{scheme}://{domain}")
        if create_vhost:
            if getattr(os, "geteuid", lambda: -1)() != 0:
                delete_website(website_id)
                raise PermissionError("Run WebPanel as root to create Apache virtual hosts.")
            try:
                vhost = await asyncio.to_thread(create_apache_vhost, domain)
                set_website_vhost(
                    website_id,
                    vhost["domain"],
                    vhost["vhost_path"],
                    vhost["document_root"],
                )
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, sqlite3.Error):
                delete_website(website_id)
                raise
        message = (
            f"{domain} is monitored and its Apache virtual host is enabled."
            if create_vhost
            else f"{domain} added to website monitoring."
        )
        ok = True
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError, sqlite3.Error) as exc:
        ok, message = False, str(exc)
    log_action(
        user,
        "WEBSITE_ADD" if ok else "WEBSITE_ADD_FAIL",
        message,
        request.client.host if request.client else "unknown",
    )
    request.session["flash"] = {
        "type": "success" if ok else "error",
        "msg": message,
    }
    return RedirectResponse("/websites", status_code=302)


@router.post("/check")
async def check_websites(request: Request, csrf_token: str = Form("")):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    await asyncio.to_thread(check_all_websites)
    log_action(
        user,
        "WEBSITES_CHECK",
        "Checked all monitored websites",
        request.client.host if request.client else "unknown",
    )
    request.session["flash"] = {"type": "success", "msg": "Website checks completed."}
    return RedirectResponse("/websites", status_code=302)


@router.post("/{website_id}/ssl")
async def install_website_ssl(
    request: Request,
    website_id: int,
    email: str = Form(""),
    confirm_domain: bool = Form(False),
    csrf_token: str = Form(""),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)

    site = next((item for item in list_websites() if item["id"] == website_id), None)
    try:
        if not site:
            raise ValueError("Website was not found.")
        user_record = get_user(user)
        site_domain = site.get("vhost_domain") or urlsplit(site.get("url") or "").hostname or ""
        if user_record and user_record.get("role") != "admin" and not user_has_domain_access(user, site_domain):
            raise ValueError("This account is restricted to its assigned domain and cannot manage that website.")
        if not confirm_domain:
            raise ValueError("Confirm that the domain points to this server and inbound ports 80 and 443 are reachable.")
        if not site.get("vhost_path") or not site.get("vhost_domain"):
            raise ValueError("Let's Encrypt requires an Apache virtual host for this website.")
        domain = validate_domain(site["vhost_domain"])
        config_path, _ = get_apache_vhost(domain)
        if Path(site["vhost_path"]).resolve(strict=True) != Path(config_path).resolve(strict=True):
            raise ValueError("The saved Apache virtual host does not match this domain.")
        await asyncio.to_thread(issue_apache_certificate, domain, email)
        ok = True
        message = (
            f"HTTPS was enabled for {domain}. Certbot stores certificates and private keys "
            "under /etc/letsencrypt and renews them automatically."
        )
    except (
        ValueError,
        OSError,
        PermissionError,
        RuntimeError,
        subprocess.SubprocessError,
        sqlite3.Error,
    ) as exc:
        ok, message = False, str(exc)

    log_action(
        user,
        "WEBSITE_SSL_INSTALL" if ok else "WEBSITE_SSL_INSTALL_FAIL",
        f"{site.get('vhost_domain', '') if site else website_id}: {'installed' if ok else 'failed'}",
        request.client.host if request.client else "unknown",
    )
    request.session["flash"] = {
        "type": "success" if ok else "error",
        "msg": message,
    }
    return RedirectResponse("/websites", status_code=302)


@router.post("/{website_id}/delete")
async def remove_website(request: Request, website_id: int, csrf_token: str = Form("")):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)

    site = next((item for item in list_websites() if item["id"] == website_id), None)
    user_record = get_user(user)
    if site and user_record and user_record.get("role") != "admin":
        site_domain = site.get("vhost_domain") or urlsplit(site.get("url") or "").hostname or ""
        if not user_has_domain_access(user, site_domain):
            request.session["flash"] = {
                "type": "error",
                "msg": "This account is restricted to its assigned domain and cannot delete that website.",
            }
            return RedirectResponse("/websites", status_code=302)

    removed = delete_website(website_id)
    message = "Website removed from monitoring." if removed else "Website not found."
    log_action(
        user,
        "WEBSITE_DELETE" if removed else "WEBSITE_DELETE_FAIL",
        str(website_id),
        request.client.host if request.client else "unknown",
    )
    request.session["flash"] = {
        "type": "success" if removed else "error",
        "msg": message,
    }
    return RedirectResponse("/websites", status_code=302)
