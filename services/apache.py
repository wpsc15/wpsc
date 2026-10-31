from services.system import run_cmd, service_status, service_action
from services.domains import validate_domain
import os
from pathlib import Path

# Ubuntu/Debian uses "apache2"; CentOS/RHEL uses "httpd"
APACHE_SERVICE = "apache2"


def get_apache_status() -> dict:
    """Return a dict with Apache service status, version, and virtual host lists."""
    status = service_status(APACHE_SERVICE)
    if status == "unknown":
        # Try httpd (CentOS/RHEL)
        status = service_status("httpd")

    _, version, _ = run_cmd("apache2 -v 2>/dev/null || httpd -v 2>/dev/null")
    _, enabled_sites, _ = run_cmd(
        "ls /etc/apache2/sites-enabled/ 2>/dev/null || ls /etc/httpd/conf.d/ 2>/dev/null"
    )
    _, available_sites, _ = run_cmd("ls /etc/apache2/sites-available/ 2>/dev/null")

    return {
        "status": status,
        "running": status == "active",
        "version": version.split("\n")[0] if version else "Not installed",
        "enabled_sites": [s for s in enabled_sites.split() if s] if enabled_sites else [],
        "available_sites": [s for s in available_sites.split() if s] if available_sites else [],
    }


def apache_action(action: str) -> tuple:
    """Perform a systemctl action on Apache. Returns (success, message)."""
    ok, msg = service_action(APACHE_SERVICE, action)
    if not ok:
        ok, msg = service_action("httpd", action)
    return ok, msg


def get_apache_config() -> str:
    """Read and return the main Apache configuration file."""
    for path in ["/etc/apache2/apache2.conf", "/etc/httpd/conf/httpd.conf"]:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return f.read()
            except PermissionError:
                return "Permission denied reading config. Run WebPanel as root."
    return "Apache config file not found."


def get_apache_config_path() -> Path:
    for candidate in (Path("/etc/apache2/apache2.conf"), Path("/etc/httpd/conf/httpd.conf")):
        if candidate.is_file():
            return candidate.resolve(strict=True)
    raise FileNotFoundError("Apache configuration file was not found.")


def get_apache_vhost(domain: str) -> tuple[str, str]:
    domain = validate_domain(domain)
    path = Path("/etc/apache2/sites-available") / f"{domain}.conf"
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(f"Apache virtual host for {domain} was not found.")
    return str(path), path.read_text(encoding="utf-8")


def get_error_log(lines: int = 50) -> str:
    """Return the last N lines of the Apache error log."""
    for path in ["/var/log/apache2/error.log", "/var/log/httpd/error_log"]:
        if os.path.exists(path):
            _, out, _ = run_cmd(f"tail -n {lines} {path}")
            return out or "(empty log)"
    return "Error log file not found."


def get_access_log(lines: int = 50) -> str:
    """Return the last N lines of the Apache access log."""
    for path in ["/var/log/apache2/access.log", "/var/log/httpd/access_log"]:
        if os.path.exists(path):
            _, out, _ = run_cmd(f"tail -n {lines} {path}")
            return out or "(empty log)"
    return "Access log file not found."
