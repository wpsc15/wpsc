from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import asyncio
import os
import shutil
from services.php_svc import (
    get_installed_php_versions,
    get_current_php_version,
    get_php_ini_path,
    read_php_ini,
    get_php_modules,
    get_php_extension_catalog,
    install_php_extension,
    get_php_fpm_pool_path,
    get_php_logs,
    get_phpinfo,
    check_phpmyadmin,
    get_phpmyadmin_config_path,
)
from services.config_editor import read_config, save_config
from services.system import run_cmd, service_action, service_status
from database import log_action
from services.web_security import get_csrf_token, require_valid_csrf
import re

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/php")
PHP_SECTIONS = (
    ("service", "Service"),
    ("extensions", "Install extensions"),
    ("configuration", "Configuration"),
    ("limit-upload", "Limit of upload"),
    ("limit-timeout", "Limit of timeout"),
    ("config-file", "Configuration file"),
    ("fpm-profile", "FPM profile"),
    ("disabled-functions", "Disabled functions"),
    ("optimization", "Optimization"),
    ("load-average", "Load average"),
    ("session", "Session configuration"),
    ("logs", "Logs"),
    ("slow-log", "Slow log"),
    ("phpinfo", "phpinfo"),
)
PHP_SETTING_KEYS = {
    "configuration": {"memory_limit", "date.timezone", "display_errors", "error_reporting"},
    "limit-upload": {"upload_max_filesize", "post_max_size", "max_file_uploads"},
    "limit-timeout": {"max_execution_time", "max_input_time"},
    "disabled-functions": {"disable_functions"},
    "optimization": {
        "opcache.enable",
        "opcache.memory_consumption",
        "opcache.interned_strings_buffer",
        "opcache.max_accelerated_files",
        "opcache.validate_timestamps",
        "opcache.revalidate_freq",
    },
    "session": {
        "session.save_path",
        "session.gc_maxlifetime",
        "session.cookie_secure",
        "session.use_strict_mode",
        "session.cookie_httponly",
    },
}


def require_auth(request: Request):
    return request.session.get("user")


@router.get("", response_class=HTMLResponse)
async def php_page(request: Request, version: str = "", section: str = "service"):
    """PHP management: versions, php.ini settings, loaded modules."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    flash = request.session.pop("flash", None)
    versions = get_installed_php_versions()
    current = get_current_php_version()
    selected_ver = version if version in versions else (versions[0] if versions else "")
    selected_section = section if section in {item[0] for item in PHP_SECTIONS} else "service"
    ini_path = get_php_ini_path(selected_ver)
    modules = get_php_modules(selected_ver)
    phpmyadmin = check_phpmyadmin()
    fpm_status = service_status(f"php{selected_ver}-fpm") if selected_ver else "not installed"
    section_error = ""
    extensions = []
    pool_path = ""
    pool_content = ""
    php_logs = ""
    log_path = ""
    php_info = ""
    try:
        if selected_ver and selected_section == "extensions":
            extensions = get_php_extension_catalog(selected_ver)
        elif selected_ver and selected_section == "fpm-profile":
            profile_path = get_php_fpm_pool_path(selected_ver)
            pool_path = str(profile_path)
            pool_content = read_config(profile_path)
        elif selected_ver and selected_section in {"logs", "slow-log"}:
            log_path, php_logs = get_php_logs(selected_ver, selected_section == "slow-log")
        elif selected_ver and selected_section == "phpinfo":
            php_info = get_phpinfo(selected_ver)
    except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
        section_error = str(exc)
    try:
        load_average = ", ".join(f"{value:.2f}" for value in os.getloadavg())
    except (AttributeError, OSError):
        load_average = "Unavailable on this platform"

    return templates.TemplateResponse(
        "php_manager.html",
        {
            "request": request,
            "user": user,
            "active": "php",
            "flash": flash,
            "versions": versions,
            "current": current,
            "selected": selected_ver,
            "ini_path": ini_path,
            "modules": modules,
            "phpmyadmin": phpmyadmin,
            "phpmyadmin_url": f"{request.url.scheme}://{request.url.hostname}/phpmyadmin/",
            "fpm_status": fpm_status,
            "ini_content": read_config(Path(ini_path)) if ini_path else "",
            "ini_settings": read_php_ini(ini_path),
            "sections": PHP_SECTIONS,
            "section": selected_section,
            "section_error": section_error,
            "extensions": extensions,
            "pool_path": pool_path,
            "pool_content": pool_content,
            "php_logs": php_logs,
            "log_path": log_path,
            "php_info": php_info,
            "load_average": load_average,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/save-config")
async def save_php_config(
    request: Request,
    version: str = Form(""),
    config: str = Form(...),
    csrf_token: str = Form(...),
):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    versions = get_installed_php_versions()
    if not version or version not in versions:
        request.session["flash"] = {"type": "error", "msg": "Choose an installed PHP version."}
        return RedirectResponse("/php", status_code=302)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        request.session["flash"] = {"type": "error", "msg": "Run WebPanel as root to edit PHP settings."}
        return RedirectResponse(f"/php?version={version}&section=config-file", status_code=302)
    try:
        ini_path = get_php_ini_path(version)
        if not ini_path:
            raise FileNotFoundError(f"php.ini for PHP {version} was not found.")
        php_binary = shutil.which(f"php{version}")
        fpm_binary = shutil.which(f"php-fpm{version}")
        if not php_binary:
            raise FileNotFoundError("PHP CLI binary was not found.")
        resolved_ini = Path(ini_path)
        validators = [[php_binary, "-c", str(resolved_ini), "-r", "exit(0);"]]
        fpm_config = Path(f"/etc/php/{version}/fpm/php-fpm.conf")
        if not fpm_binary or not fpm_config.is_file():
            raise FileNotFoundError(f"PHP-FPM {version} configuration was not found.")
        validators.append([fpm_binary, "-t", "-y", str(fpm_config)])
        ok, message = save_config(
            resolved_ini,
            config,
            validators,
            [["systemctl", "restart", f"php{version}-fpm"]],
        )
    except (OSError, UnicodeError) as exc:
        ok, message = False, str(exc)
    log_action(user, "PHP_CONFIG_SAVE", f"version={version}; {message}", request.client.host if request.client else "unknown")
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse(f"/php?version={version}&section=config-file", status_code=302)


@router.post("/save-setting")
async def save_php_setting(
    request: Request,
    version: str = Form(...),
    section: str = Form(...),
    key: str = Form(...),
    value: str = Form(...),
    csrf_token: str = Form(...),
):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    redirect = f"/php?version={version}&section={section}"
    if version not in get_installed_php_versions():
        request.session["flash"] = {"type": "error", "msg": "Choose an installed PHP version."}
        return RedirectResponse("/php", status_code=302)
    if section not in PHP_SETTING_KEYS or key not in PHP_SETTING_KEYS[section]:
        request.session["flash"] = {"type": "error", "msg": "Choose a supported PHP setting."}
        return RedirectResponse(redirect, status_code=302)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        request.session["flash"] = {"type": "error", "msg": "Run WebPanel as root to edit PHP settings."}
        return RedirectResponse(redirect, status_code=302)

    if key in {"upload_max_filesize", "post_max_size", "memory_limit"}:
        valid = re.fullmatch(r"\d+(?:[KMG])?", value, re.IGNORECASE)
    elif key in {
        "max_execution_time", "max_input_time", "max_file_uploads",
        "opcache.memory_consumption", "opcache.interned_strings_buffer",
        "opcache.max_accelerated_files", "opcache.revalidate_freq",
        "session.gc_maxlifetime",
    }:
        valid = re.fullmatch(r"\d+", value)
    elif key in {
        "display_errors", "opcache.enable", "opcache.validate_timestamps",
        "session.cookie_secure", "session.use_strict_mode",
        "session.cookie_httponly",
    }:
        valid = value.lower() in {"0", "1", "on", "off"}
    elif key == "date.timezone":
        valid = bool(re.fullmatch(r"[A-Za-z0-9_+./-]{1,100}", value))
    elif key == "session.save_path":
        valid = bool(re.fullmatch(r"[A-Za-z0-9_./-]{0,200}", value))
    elif key == "disable_functions":
        valid = bool(re.fullmatch(r"[A-Za-z0-9_,\s-]{0,4000}", value))
    else:
        valid = bool(re.fullmatch(r"[A-Za-z0-9_&|!~().\s-]{1,300}", value))
    if not valid:
        request.session["flash"] = {"type": "error", "msg": "Invalid value for this PHP setting."}
        return RedirectResponse(redirect, status_code=302)

    try:
        main_ini_path = Path(get_php_ini_path(version))
        ini_path = main_ini_path
        if key.startswith("opcache."):
            opcache_config = Path(f"/etc/php/{version}/fpm/conf.d/10-opcache.ini")
            ini_path = opcache_config.resolve(strict=True)
            if not ini_path.is_relative_to(Path(f"/etc/php/{version}")):
                raise ValueError("The PHP OPcache configuration path is outside the PHP configuration directory.")
        content = read_config(ini_path)
        setting_pattern = re.compile(
            rf"^(?!\s*;)(\s*{re.escape(key)}\s*=\s*).*$",
            re.MULTILINE | re.IGNORECASE,
        )
        content, count = setting_pattern.subn(lambda match: f"{match.group(1)}{value}", content)
        if count == 0:
            content += f"\n{key} = {value}\n"
        php_binary = shutil.which(f"php{version}")
        if not php_binary:
            raise FileNotFoundError("PHP CLI binary was not found.")
        fpm_binary = shutil.which(f"php-fpm{version}")
        fpm_config = Path(f"/etc/php/{version}/fpm/php-fpm.conf")
        if not fpm_binary or not fpm_config.is_file():
            raise FileNotFoundError(f"PHP-FPM {version} configuration was not found.")
        ok, message = save_config(
            ini_path,
            content,
            [
                [php_binary, "-c", str(main_ini_path), "-r", "exit(0);"],
                [fpm_binary, "-t", "-y", str(fpm_config)],
            ],
            [["systemctl", "restart", f"php{version}-fpm"]],
        )
    except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
        ok, message = False, str(exc)
    log_action(user, "PHP_SETTING_SAVE", f"{key}; {message}", request.client.host if request.client else "unknown")
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse(redirect, status_code=302)


@router.post("/save-pool")
async def save_php_pool(
    request: Request,
    version: str = Form(...),
    config: str = Form(...),
    csrf_token: str = Form(...),
):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    redirect = f"/php?version={version}&section=fpm-profile"
    if version not in get_installed_php_versions():
        request.session["flash"] = {"type": "error", "msg": "Choose an installed PHP version."}
        return RedirectResponse("/php", status_code=302)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        request.session["flash"] = {"type": "error", "msg": "Run WebPanel as root to edit FPM profiles."}
        return RedirectResponse(redirect, status_code=302)
    try:
        path = get_php_fpm_pool_path(version)
        fpm_binary = shutil.which(f"php-fpm{version}")
        fpm_config = path.parent.parent / "php-fpm.conf"
        if not fpm_binary or not fpm_config.is_file():
            raise FileNotFoundError(f"PHP-FPM {version} configuration was not found.")
        ok, message = save_config(
            path,
            config,
            [[fpm_binary, "-t", "-y", str(fpm_config)]],
            [["systemctl", "restart", f"php{version}-fpm"]],
        )
    except (OSError, UnicodeError, RuntimeError) as exc:
        ok, message = False, str(exc)
    log_action(user, "PHP_FPM_PROFILE_SAVE", f"version={version}; {message}", request.client.host if request.client else "unknown")
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse(redirect, status_code=302)


@router.post("/install-extension")
async def php_install_extension(
    request: Request,
    version: str = Form(...),
    extension: str = Form(...),
    csrf_token: str = Form(...),
):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    redirect = f"/php?version={version}&section=extensions"
    if version not in get_installed_php_versions():
        request.session["flash"] = {"type": "error", "msg": "Choose an installed PHP version."}
        return RedirectResponse("/php", status_code=302)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        request.session["flash"] = {"type": "error", "msg": "Run WebPanel as root to install PHP extensions."}
        return RedirectResponse(redirect, status_code=302)
    try:
        catalog = get_php_extension_catalog(version)
        selected = next(item for item in catalog if item["name"] == extension)
        if not selected["available"]:
            raise ValueError(f"{selected['package']} is not available in the configured repositories.")
        if selected["installed"]:
            raise ValueError(f"{selected['package']} is already installed.")
        ok, message = await asyncio.to_thread(install_php_extension, version, extension)
        if ok:
            restarted, restart_message = service_action(f"php{version}-fpm", "restart")
            if not restarted:
                ok = False
                message = f"Extension installed, but PHP-FPM could not restart: {restart_message}"
    except (OSError, RuntimeError, ValueError, StopIteration) as exc:
        ok, message = False, str(exc)
    log_action(user, "PHP_EXTENSION_INSTALL", f"php{version}-{extension}; {message}", request.client.host if request.client else "unknown")
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse(redirect, status_code=302)


@router.post("/service-action")
async def php_service_control(
    request: Request,
    version: str = Form(...),
    action: str = Form(...),
    csrf_token: str = Form(...),
):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    require_valid_csrf(request, csrf_token)
    if version not in get_installed_php_versions():
        request.session["flash"] = {"type": "error", "msg": "Choose an installed PHP version."}
        return RedirectResponse("/php", status_code=302)
    if action not in {"start", "stop", "restart", "reload"}:
        request.session["flash"] = {"type": "error", "msg": "Invalid PHP service action."}
        return RedirectResponse(f"/php?version={version}&section=service", status_code=302)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        request.session["flash"] = {"type": "error", "msg": "Run WebPanel as root to manage PHP-FPM."}
        return RedirectResponse(f"/php?version={version}&section=service", status_code=302)

    service = f"php{version}-fpm"
    ok, message = service_action(service, action)
    log_action(
        user,
        f"PHP_FPM_{action.upper()}",
        f"{service}: {message}",
        request.client.host if request.client else "unknown",
    )
    request.session["flash"] = {
        "type": "success" if ok else "error",
        "msg": f"{service} {action}: {'OK' if ok else message}",
    }
    return RedirectResponse(f"/php?version={version}&section=service", status_code=302)


@router.get("/phpmyadmin", response_class=HTMLResponse)
async def phpmyadmin_page(request: Request):
    """phpMyAdmin management: installation status and Apache config toggle."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    flash = request.session.pop("flash", None)
    pma = check_phpmyadmin()
    return templates.TemplateResponse(
        "phpmyadmin.html",
        {
            "request": request,
            "user": user,
            "active": "phpmyadmin",
            "flash": flash,
            "pma": pma,
            "phpmyadmin_url": f"{request.url.scheme}://{request.url.hostname}/phpmyadmin/",
            "config_path": "",
            "config": "",
            "config_error": "",
        },
    )


@router.post("/phpmyadmin/toggle")
async def toggle_phpmyadmin(request: Request, action: str = Form(...)):
    """Enable or disable the phpMyAdmin Apache config."""
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        request.session["flash"] = {"type": "error", "msg": "Run WebPanel as root to manage Apache configuration."}
        return RedirectResponse("/php/phpmyadmin", status_code=302)

    if action == "enable":
        code, _, err = run_cmd(
            "a2enconf phpmyadmin && systemctl reload apache2"
        )
        ok = code == 0
    elif action == "disable":
        code, _, err = run_cmd(
            "a2disconf phpmyadmin && systemctl reload apache2"
        )
        ok = code == 0
    else:
        ok, err = False, "Invalid action"

    ip = request.client.host if request.client else "unknown"
    log_action(user, f"PHPMYADMIN_{action.upper()}", "", ip)
    request.session["flash"] = {
        "type": "success" if ok else "error",
        "msg": f"phpMyAdmin {action}: {'OK' if ok else err}",
    }
    return RedirectResponse("/php/phpmyadmin", status_code=302)


@router.get("/phpmyadmin/config", response_class=HTMLResponse)
async def phpmyadmin_config(request: Request):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        path = get_phpmyadmin_config_path()
        config = read_config(path)
        error = ""
    except (OSError, UnicodeError) as exc:
        path, config, error = None, "", str(exc)
    return templates.TemplateResponse(
        "phpmyadmin.html",
        {
            "request": request,
            "user": user,
            "active": "phpmyadmin",
            "flash": request.session.pop("flash", None),
            "pma": check_phpmyadmin(),
            "phpmyadmin_url": f"{request.url.scheme}://{request.url.hostname}/phpmyadmin/",
            "config_path": str(path) if path else "",
            "config": config,
            "config_error": error,
        },
    )


@router.post("/phpmyadmin/config")
async def save_phpmyadmin_config(request: Request, config: str = Form(...)):
    user = require_auth(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        request.session["flash"] = {"type": "error", "msg": "Run WebPanel as root to edit system configuration."}
        return RedirectResponse("/php/phpmyadmin/config", status_code=302)
    try:
        path = get_phpmyadmin_config_path()
        php_binary = shutil.which("php")
        if not php_binary:
            raise FileNotFoundError("PHP CLI binary was not found.")
        ok, message = save_config(path, config, [[php_binary, "-l", str(path)]])
    except (OSError, UnicodeError) as exc:
        ok, message = False, str(exc)
    log_action(user, "PHPMYADMIN_CONFIG_SAVE", message, request.client.host if request.client else "unknown")
    request.session["flash"] = {"type": "success" if ok else "error", "msg": message}
    return RedirectResponse("/php/phpmyadmin/config", status_code=302)
