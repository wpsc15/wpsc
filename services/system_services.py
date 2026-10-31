import os
import re
import shutil
import subprocess


SERVICE_LABELS = {
    "apache2": ("Apache", "Web server"),
    "httpd": ("Apache", "Web server"),
    "nginx": ("Nginx", "Web server"),
    "mysql": ("MySQL", "Database"),
    "mariadb": ("MariaDB", "Database"),
    "docker": ("Docker", "Containers"),
    "containerd": ("containerd", "Containers"),
    "vsftpd": ("vsftpd", "FTP"),
    "proftpd": ("ProFTPD", "FTP"),
    "pure-ftpd": ("Pure-FTPd", "FTP"),
    "postfix": ("Postfix", "Mail"),
    "dovecot": ("Dovecot", "Mail"),
    "redis-server": ("Redis", "Database"),
    "redis": ("Redis", "Database"),
    "cron": ("Cron", "System"),
    "crond": ("Cron", "System"),
    "fail2ban": ("Fail2ban", "Security"),
    "ufw": ("UFW firewall", "Security"),
    "firewalld": ("Firewalld", "Security"),
    "bind9": ("BIND DNS", "DNS"),
    "named": ("BIND DNS", "DNS"),
}
PHP_FPM_UNIT = re.compile(r"php\d+(?:\.\d+)?-fpm\.service$")


def _run_systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )


def list_managed_services() -> list[dict[str, str]]:
    """Return installed hosting-related services from the fixed supported set."""
    if not shutil.which("systemctl"):
        raise RuntimeError("systemctl is unavailable; this server cannot manage systemd services.")

    try:
        result = _run_systemctl(
            "list-unit-files", "--type=service", "--no-legend", "--no-pager"
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not list system services: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(message or "systemctl could not list service units.")

    installed_units = {
        line.split()[0]
        for line in result.stdout.splitlines()
        if line.split()
    }
    services = []
    for unit in sorted(installed_units):
        if not unit.endswith(".service"):
            continue
        service_id = unit.removesuffix(".service")
        if service_id in SERVICE_LABELS:
            label, category = SERVICE_LABELS[service_id]
        elif PHP_FPM_UNIT.fullmatch(unit):
            version = service_id.removeprefix("php").removesuffix("-fpm")
            label, category = f"PHP-FPM {version}", "PHP"
        else:
            continue

        try:
            status = _run_systemctl(
                "show", "--property=ActiveState", "--value", unit
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not read status for {unit}: {exc}") from exc
        if status.returncode != 0:
            message = status.stderr.strip() or status.stdout.strip()
            raise RuntimeError(message or f"Could not read status for {unit}.")
        services.append(
            {
                "unit": unit,
                "name": label,
                "category": category,
                "status": status.stdout.strip() or "unknown",
            }
        )
    return services


def manage_service(unit: str, action: str) -> tuple[bool, str]:
    """Run an allowed systemd action against an installed, supported service."""
    if action not in {"start", "stop", "restart"}:
        return False, "Unsupported service action."
    try:
        installed_services = list_managed_services()
    except RuntimeError as exc:
        return False, str(exc)
    if unit not in {service["unit"] for service in installed_services}:
        return False, "Service is not installed or is not supported by this manager."
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False, "Run WebPanel as root to manage system services."

    try:
        result = _run_systemctl(action, unit)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Could not {action} {unit}: {exc}"

    if result.returncode == 0:
        return True, f"{unit} {action} completed."
    message = result.stderr.strip() or result.stdout.strip()
    return False, message or f"systemctl {action} {unit} failed."
