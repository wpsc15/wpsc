import gzip
import glob
import re
import shutil
import subprocess
from collections import deque
from pathlib import Path

from services.system_services import list_managed_services

MAX_LOG_LINES = 200
MAX_LOG_BYTES = 262_144
SAFE_UNIT = re.compile(r"[A-Za-z0-9_.@:-]+\.service$")
PHP_FPM_UNIT = re.compile(r"php(\d+(?:\.\d+)?)-fpm\.service$")
SERVICE_LOG_PATTERNS = {
    "apache2": (("/var/log/apache2/*.log*", None),),
    "httpd": (("/var/log/httpd/*_log*", None),),
    "mysql": (("/var/log/mysql/*.log*", None), ("/var/log/mysqld.log*", None)),
    "mariadb": (("/var/log/mysql/*.log*", None), ("/var/log/mariadb/*.log*", None)),
    "nginx": (("/var/log/nginx/*.log*", None),),
    "ufw": (("/var/log/ufw.log*", None), ("/var/log/syslog*", r"\bUFW\b")),
    "firewalld": (("/var/log/firewalld*", None), ("/var/log/syslog*", r"\bfirewalld\b")),
    "cron": (("/var/log/cron.log*", None), ("/var/log/syslog*", r"\bCRON\b")),
    "crond": (("/var/log/cron*", None), ("/var/log/syslog*", r"\bCROND?\b")),
    "docker": (("/var/log/docker.log*", None), ("/var/log/syslog*", r"\bdockerd\b")),
    "containerd": (("/var/log/containerd*.log*", None), ("/var/log/syslog*", r"\bcontainerd\b")),
    "vsftpd": (("/var/log/vsftpd*.log*", None), ("/var/log/syslog*", r"\bvsftpd\b")),
    "proftpd": (("/var/log/proftpd/*.log*", None), ("/var/log/syslog*", r"\bproftpd\b")),
    "pure-ftpd": (("/var/log/pure-ftpd/*.log*", None), ("/var/log/syslog*", r"\bpure-ftpd\b")),
    "postfix": (("/var/log/mail.log*", r"\bpostfix\b"), ("/var/log/maillog*", r"\bpostfix\b")),
    "dovecot": (("/var/log/mail.log*", r"\bdovecot\b"), ("/var/log/maillog*", r"\bdovecot\b")),
    "redis-server": (("/var/log/redis/*.log*", None),),
    "redis": (("/var/log/redis/*.log*", None),),
    "fail2ban": (("/var/log/fail2ban.log*", None),),
    "bind9": (("/var/log/named/*.log*", None), ("/var/log/syslog*", r"\bnamed\b")),
    "named": (("/var/log/named/*.log*", None), ("/var/log/syslog*", r"\bnamed\b")),
}


def available_log_services() -> list[dict[str, str]]:
    return list_managed_services()


def _service_log_patterns(unit: str) -> tuple[tuple[str, str | None], ...]:
    php_match = PHP_FPM_UNIT.fullmatch(unit)
    if php_match:
        version = php_match.group(1)
        return (
            (f"/var/log/php{version}-fpm.log*", None),
            (f"/var/log/php/{version}/fpm.log*", None),
        )
    return SERVICE_LOG_PATTERNS.get(unit.removesuffix(".service"), ())


def _read_service_file_logs(unit: str) -> str:
    sections = []
    seen_paths = set()
    for pattern, line_pattern in _service_log_patterns(unit):
        matcher = re.compile(line_pattern) if line_pattern else None
        for filename in sorted(glob.glob(pattern)):
            path = Path(filename)
            if filename in seen_paths or not path.is_file():
                continue
            seen_paths.add(filename)
            try:
                opener = gzip.open if filename.endswith(".gz") else open
                with opener(path, "rt", encoding="utf-8", errors="replace") as log_file:
                    lines = deque(
                        (
                            line.rstrip("\r\n")
                            for line in log_file
                            if matcher is None or matcher.search(line)
                        ),
                        maxlen=MAX_LOG_LINES,
                    )
            except (OSError, EOFError, gzip.BadGzipFile) as exc:
                raise RuntimeError(f"Could not read service log file {path}: {exc}") from exc
            if lines:
                sections.append(f"--- {path} ---\n" + "\n".join(lines))
    return "\n\n".join(sections)


def _clear_service_file_logs(unit: str) -> None:
    for pattern, line_pattern in _service_log_patterns(unit):
        if line_pattern is not None:
            continue
        for filename in glob.glob(pattern):
            path = Path(filename)
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if filename.endswith(".gz"):
                    path.unlink()
                else:
                    path.write_bytes(b"")
            except OSError as exc:
                raise RuntimeError(f"Could not clear service log file {path}: {exc}") from exc


def read_service_log(unit: str) -> str:
    if not SAFE_UNIT.fullmatch(unit):
        raise ValueError("Select a supported service.")
    if not shutil.which("journalctl"):
        raise RuntimeError("journalctl is not installed on this server.")
    if unit not in {service["unit"] for service in available_log_services()}:
        raise ValueError("Logs are available only for installed, supported services.")

    try:
        result = subprocess.run(
            [
                "journalctl",
                "--unit",
                unit,
                "--lines",
                str(MAX_LOG_LINES),
                "--no-pager",
                "--output",
                "short-iso",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not read logs for {unit}: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(message or f"journalctl could not read logs for {unit}.")
    journal_output = result.stdout.strip()
    if journal_output == "-- No entries --":
        journal_output = ""
    file_output = _read_service_file_logs(unit)
    output = "\n\n".join(part for part in (journal_output, file_output) if part)
    encoded = output.encode("utf-8", errors="replace")
    if len(encoded) > MAX_LOG_BYTES:
        output = "[Earlier log output omitted to fit the 256 KiB limit.]\n"
        output += encoded[-MAX_LOG_BYTES:].decode("utf-8", errors="replace")
    return output


def clear_server_journal(unit: str) -> None:
    if not SAFE_UNIT.fullmatch(unit):
        raise ValueError("Select a supported service.")
    if unit not in {service["unit"] for service in available_log_services()}:
        raise ValueError("Logs are available only for installed, supported services.")
    if not shutil.which("journalctl"):
        raise RuntimeError("journalctl is not installed on this server.")

    try:
        result = subprocess.run(
            ["journalctl", "--rotate", "--vacuum-size=1"],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not clear the server journal: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(message or "journalctl could not clear the server journal.")
    _clear_service_file_logs(unit)
