from services.system import run_cmd
import os
import re
import shutil
import subprocess
from collections import deque
from pathlib import Path

PHP_EXTENSION_ALLOWLIST = (
    "bcmath",
    "bz2",
    "intl",
    "ldap",
    "opcache",
    "pgsql",
    "soap",
    "sqlite3",
    "tidy",
    "xsl",
)


def get_installed_php_versions() -> list:
    """Return sorted list of installed PHP versions, e.g. ['8.2', '8.1', '7.4']."""
    versions = []
    _, out, _ = run_cmd(
        "find /usr/bin /usr/local/bin -name 'php*' 2>/dev/null | sort"
    )
    for line in out.split("\n"):
        match = re.search(r"php(\d+\.\d+)$", line)
        if match:
            v = match.group(1)
            if v not in versions:
                versions.append(v)

    if not versions:
        # Fallback: try the default php binary
        _, out, _ = run_cmd("php -v 2>/dev/null | head -1")
        if "PHP" in out:
            match = re.search(r"PHP (\d+\.\d+)", out)
            if match:
                versions.append(match.group(1))

    return sorted(set(versions), reverse=True)


def get_current_php_version() -> str:
    """Return full version string of the default PHP binary, e.g. '8.2.12'."""
    _, out, _ = run_cmd("php -v 2>/dev/null | head -1")
    match = re.search(r"PHP (\d+\.\d+\.\d+)", out)
    return match.group(1) if match else "unknown"


def get_php_ini_path(version: str = "") -> str:
    """Return the path to the relevant php.ini file for the given version."""
    candidates = []
    if version:
        candidates = [
            f"/etc/php/{version}/fpm/php.ini",
            f"/etc/php/{version}/apache2/php.ini",
            f"/etc/php/{version}/cli/php.ini",
        ]
    candidates += ["/etc/php/php.ini", "/etc/php.ini"]

    for p in candidates:
        if os.path.exists(p):
            return p

    # Last resort: ask PHP itself
    _, out, _ = run_cmd(
        "php -i 2>/dev/null | grep 'Loaded Configuration File' | awk '{print $NF}'"
    )
    return out.strip() if out.strip() and out.strip() != "(none)" else ""


def read_php_ini(path: str) -> dict:
    """Parse key php.ini settings and return their current values."""
    settings = {
        "upload_max_filesize": "2M",
        "post_max_size": "8M",
        "max_execution_time": "30",
        "max_input_time": "60",
        "memory_limit": "128M",
        "display_errors": "Off",
        "error_reporting": "E_ALL & ~E_DEPRECATED & ~E_STRICT",
        "date.timezone": "UTC",
        "max_file_uploads": "20",
        "disable_functions": "",
        "opcache.enable": "1",
        "opcache.memory_consumption": "128",
        "opcache.interned_strings_buffer": "8",
        "opcache.max_accelerated_files": "10000",
        "opcache.validate_timestamps": "1",
        "opcache.revalidate_freq": "2",
        "session.save_path": "",
        "session.gc_maxlifetime": "1440",
        "session.cookie_secure": "0",
        "session.use_strict_mode": "0",
        "session.cookie_httponly": "1",
    }
    if not path or not os.path.exists(path):
        return settings
    config_path = Path(path)
    config_files = [config_path]
    scan_dir = config_path.parent / "conf.d"
    if scan_dir.is_dir():
        config_files.extend(sorted(scan_dir.glob("*.ini")))
    for config_file in config_files:
        if config_file.is_symlink() and config_file.parent.name != "conf.d":
            continue
        with config_file.open(encoding="utf-8", errors="replace") as f:
            content = f.read()
        for key in settings:
            pattern = rf"^\s*{re.escape(key)}\s*=\s*(.+)$"
            match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
            if match:
                settings[key] = match.group(1).strip().strip('"').strip("'")
    return settings


def write_php_ini_setting(path: str, key: str, value: str) -> tuple:
    """Write or append a single php.ini key=value. Returns (success, message)."""
    if not path or not os.path.exists(path):
        return False, "php.ini not found"
    try:
        with open(path) as f:
            content = f.read()
        pattern = rf"^(\s*{re.escape(key)}\s*=\s*)(.+)$"
        new_content, count = re.subn(
            pattern, rf"\g<1>{value}", content, flags=re.MULTILINE | re.IGNORECASE
        )
        if count == 0:
            new_content += f"\n{key} = {value}\n"
        with open(path, "w") as f:
            f.write(new_content)
        return True, "Saved"
    except PermissionError:
        return False, "Permission denied. Run WebPanel as root."
    except Exception as e:
        return False, str(e)


def get_php_modules(version: str = "") -> list:
    """Return a list of loaded PHP extension/module names."""
    cmd = f"php{version} -m 2>/dev/null" if version else "php -m 2>/dev/null"
    _, out, _ = run_cmd(cmd)
    return [m for m in out.strip().split("\n") if m and not m.startswith("[")]


def get_php_extension_catalog(version: str) -> list[dict[str, str | bool]]:
    if not re.fullmatch(r"\d+\.\d+", version):
        raise ValueError("Choose a valid PHP version.")
    modules = {module.lower() for module in get_php_modules(version)}
    apt_cache = shutil.which("apt-cache")
    if not apt_cache:
        raise FileNotFoundError("apt-cache is not available on this server.")
    extensions = []
    for extension in PHP_EXTENSION_ALLOWLIST:
        package = f"php{version}-{extension}"
        try:
            result = subprocess.run(
                [apt_cache, "madison", package],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not check the {package} package: {exc}") from exc
        if result.returncode:
            raise RuntimeError(
                result.stderr.strip() or f"Could not check the {package} package."
            )
        extensions.append({
            "name": extension,
            "package": package,
            "installed": extension in modules or (
                extension == "opcache" and any("opcache" in module for module in modules)
            ),
            "available": "|" in result.stdout,
        })
    return extensions


def install_php_extension(version: str, extension: str) -> tuple[bool, str]:
    if not re.fullmatch(r"\d+\.\d+", version):
        return False, "Choose a valid PHP version."
    if extension not in PHP_EXTENSION_ALLOWLIST:
        return False, "Choose a supported PHP extension."
    apt_get = shutil.which("apt-get")
    if not apt_get:
        return False, "apt-get is not available on this server."
    package = f"php{version}-{extension}"
    try:
        result = subprocess.run(
            [apt_get, "install", "-y", "--no-install-recommends", package],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Could not install {package}: {exc}"
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode:
        return False, output[-4000:] or f"apt-get could not install {package}."
    return True, output[-4000:] or f"Installed {package}."


def get_php_fpm_pool_path(version: str) -> Path:
    if not re.fullmatch(r"\d+\.\d+", version):
        raise ValueError("Choose a valid PHP version.")
    return Path(f"/etc/php/{version}/fpm/pool.d/www.conf")


def get_php_logs(version: str, slow: bool = False, limit: int = 150) -> tuple[str, str]:
    if not re.fullmatch(r"\d+\.\d+", version):
        raise ValueError("Choose a valid PHP version.")
    log_path = Path(f"/var/log/php{version}-fpm.log")
    if slow:
        pool_path = get_php_fpm_pool_path(version)
        configured_path = ""
        if pool_path.is_file() and not pool_path.is_symlink():
            for line in pool_path.read_text(encoding="utf-8", errors="replace").splitlines():
                match = re.match(r"^\s*slowlog\s*=\s*(\S+)", line)
                if match:
                    configured_path = match.group(1)
                    break
        if not configured_path:
            return str(pool_path), "Slow logging is not enabled. Set slowlog and request_slowlog_timeout in the FPM profile, then restart PHP-FPM."
        configured_log = Path(configured_path)
        if not configured_log.is_absolute() or Path("/var/log") not in configured_log.parents:
            raise ValueError("The configured PHP slow log must be located under /var/log.")
        log_path = configured_log
    if log_path.is_symlink() or not log_path.is_file():
        return str(log_path), "No log file is present at this path."
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 512 * 1024))
            content = handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise RuntimeError(f"Could not read the PHP-FPM log: {exc}") from exc
    return str(log_path), "\n".join(deque(content.splitlines(), maxlen=limit))


def get_phpinfo(version: str) -> str:
    if not re.fullmatch(r"\d+\.\d+", version):
        raise ValueError("Choose a valid PHP version.")
    binary = shutil.which(f"php{version}")
    if not binary:
        raise FileNotFoundError(f"PHP {version} CLI binary was not found.")
    try:
        result = subprocess.run(
            [binary, "-i"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not read PHP {version} information: {exc}") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"PHP {version} info command failed.")
    return result.stdout[-64000:]


def check_phpmyadmin() -> dict:
    """Check whether phpMyAdmin is installed and its Apache config status."""
    paths = [
        "/usr/share/phpmyadmin",
        "/var/www/html/phpmyadmin",
        "/opt/phpmyadmin",
    ]
    apache_conf_enabled = "/etc/apache2/conf-enabled/phpmyadmin.conf"
    apache_conf_paths = [
        apache_conf_enabled,
        "/etc/apache2/conf-available/phpmyadmin.conf",
        "/etc/httpd/conf.d/phpMyAdmin.conf",
    ]

    installed_path = None
    for p in paths:
        if os.path.exists(p):
            installed_path = p
            break

    apache_enabled = os.path.exists(apache_conf_enabled)
    apache_available = any(os.path.exists(p) for p in apache_conf_paths)

    return {
        "installed": installed_path is not None,
        "path": installed_path or "",
        "apache_enabled": apache_enabled,
        "apache_available": apache_available,
        "url": "/phpmyadmin",
    }


def get_phpmyadmin_config_path() -> Path:
    for candidate in (
        Path("/etc/phpmyadmin/config.inc.php"),
        Path("/usr/share/phpmyadmin/config.inc.php"),
    ):
        if candidate.is_file():
            return candidate.resolve(strict=True)
    raise FileNotFoundError("phpMyAdmin configuration file was not found.")
