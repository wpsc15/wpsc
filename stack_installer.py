import os
import logging
import re
import signal
import shutil
import subprocess
import threading
import uuid
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "install_component.sh"
logger = logging.getLogger(__name__)
PHP_PACKAGE_PATTERN = re.compile(r"^php(\d+\.\d+)$")
PACKAGE_COMPONENTS = {
    "apache": "apache2",
    "mysql": "mysql-server",
    "phpmyadmin": "phpmyadmin",
    "nginx": "nginx",
    "redis": "redis-server",
    "nodejs": "nodejs",
    "git": "git",
    "composer": "composer",
    "certbot": "certbot",
    "memcached": "memcached",
    "htop": "htop",
    "fail2ban": "fail2ban",
    "unzip": "unzip",
    "postgresql": "postgresql",
    "sqlite": "sqlite3",
    "supervisor": "supervisor",
    "rabbitmq": "rabbitmq-server",
    "varnish": "varnish",
    "haproxy": "haproxy",
    "java": "default-jre-headless",
    "go": "golang-go",
    "ruby": "ruby",
    "rsync": "rsync",
    "curl": "curl",
    "wget": "wget",
    "nano": "nano",
    "vim": "vim-tiny",
    "clamav": "clamav",
}
COMPONENTS = {*PACKAGE_COMPONENTS, "php", "docker"}
ADDITIONAL_PACKAGE_COMPONENTS = (
    "nginx",
    "redis",
    "nodejs",
    "git",
    "composer",
    "certbot",
    "memcached",
    "htop",
    "fail2ban",
    "unzip",
    "postgresql",
    "sqlite",
    "supervisor",
    "rabbitmq",
    "varnish",
    "haproxy",
    "java",
    "go",
    "ruby",
    "rsync",
    "curl",
    "wget",
    "nano",
    "vim",
    "clamav",
)
_job_lock = threading.Lock()
_job = {
    "id": "",
    "owner": "",
    "component": "",
    "version": "",
    "status": "idle",
    "output": "",
}


def _kill_install_process(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _run_output(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{args[0]} failed.")
    return result.stdout


def _package_versions(package: str) -> list[str]:
    versions = []
    for line in _run_output(["apt-cache", "madison", package]).splitlines():
        fields = line.split("|")
        if len(fields) >= 2:
            version = fields[1].strip()
            if version and version not in versions:
                versions.append(version)
    return versions


def _version_is_newer(candidate: str, installed: str) -> bool:
    dpkg = shutil.which("dpkg")
    if not dpkg:
        logger.warning("Cannot compare package versions because dpkg is unavailable")
        return False
    try:
        result = subprocess.run(
            [dpkg, "--compare-versions", candidate, "gt", installed],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Could not compare installed and repository package versions: %s", exc)
        return False
    if result.returncode not in {0, 1}:
        logger.warning("dpkg could not compare package versions: %s", result.stderr.strip())
        return False
    return result.returncode == 0


def _supported_os_id() -> str:
    release = Path("/etc/os-release")
    if not release.exists():
        return ""
    for line in release.read_text(encoding="utf-8").splitlines():
        if line.startswith("ID="):
            return line.split("=", 1)[1].strip().strip('"').lower()
    return ""


def get_docker_options() -> dict:
    """Return Docker Engine package versions available from configured apt sources."""
    if _supported_os_id() not in {"debian", "ubuntu"}:
        return {"supported": False, "versions": [], "error": ""}
    try:
        return {
            "supported": True,
            "versions": _package_versions("docker.io"),
            "error": "",
        }
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        logger.exception("Could not read available Docker package versions")
        return {"supported": True, "versions": [], "error": str(exc)}


def get_stack_options() -> dict:
    """Return Debian/Ubuntu package versions currently visible to apt."""
    os_id = _supported_os_id()
    if os_id not in {"debian", "ubuntu"}:
        return {
            "supported": False,
            "apache": [],
            "mysql": [],
            "docker": [],
            "php": [],
            "phpmyadmin": [],
            **{component: [] for component in ADDITIONAL_PACKAGE_COMPONENTS},
        }

    try:
        apache = _package_versions("apache2")
        mysql = _package_versions("mysql-server")
        docker = _package_versions("docker.io")
        phpmyadmin = _package_versions("phpmyadmin")
        additional_packages = {
            component: _package_versions(PACKAGE_COMPONENTS[component])
            for component in ADDITIONAL_PACKAGE_COMPONENTS
        }
        packages = _run_output(["apt-cache", "pkgnames"]).splitlines()
        php = []
        extensions = ("cli", "fpm", "mysql", "curl", "xml", "mbstring", "zip", "gd")
        for package in packages:
            match = PHP_PACKAGE_PATTERN.fullmatch(package)
            if match and _package_versions(package) and all(
                _package_versions(f"{package}-{extension}")
                for extension in extensions
            ):
                php.append(match.group(1))
        return {
            "supported": True,
            "apache": apache,
            "mysql": mysql,
            "docker": docker,
            "phpmyadmin": phpmyadmin,
            **additional_packages,
            "php": sorted(
                set(php),
                key=lambda version: tuple(map(int, version.split("."))),
                reverse=True,
            ),
        }
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        logger.exception("Could not read available apt package versions")
        return {
            "supported": True,
            "apache": [],
            "mysql": [],
            "docker": [],
            "phpmyadmin": [],
            "php": [],
            **{component: [] for component in ADDITIONAL_PACKAGE_COMPONENTS},
            "error": str(exc),
        }


def get_app_catalog(options: dict, docker_options: dict) -> list[dict]:
    """Build the App Store from allowlisted apps and the server's installed packages."""
    app_definitions = (
        ("apache", "Apache", "apache2", "Deployment", "fa-globe", "/etc/apache2", "https://httpd.apache.org/docs/", "/apache/config"),
        ("nginx", "Nginx", "nginx", "Deployment", "fa-globe", "/etc/nginx", "https://nginx.org/en/docs/", "/services"),
        ("mysql", "MySQL Server", "mysql-server", "Deployment", "fa-database", "/var/lib/mysql", "https://dev.mysql.com/doc/", "/mysql"),
        ("php", "PHP + PHP-FPM", "", "Deployment", "fa-php", "/etc/php", "https://www.php.net/manual/", "/php"),
        ("nodejs", "Node.js", "nodejs", "Deployment", "fa-code", "/usr/lib/node_modules", "https://nodejs.org/docs/", "/services"),
        ("redis", "Redis Server", "redis-server", "Deployment", "fa-database", "/etc/redis", "https://redis.io/docs/latest/", "/services"),
        ("memcached", "Memcached", "memcached", "Deployment", "fa-memory", "/etc/memcached.conf", "https://docs.memcached.org/", "/services"),
        ("docker", "Docker Engine", "docker.io", "Deployment", "fa-docker", "/var/lib/docker", "https://docs.docker.com/engine/", "/docker"),
        ("phpmyadmin", "phpMyAdmin", "phpmyadmin", "Tools", "fa-table-columns", "/usr/share/phpmyadmin", "https://docs.phpmyadmin.net/", "/php/phpmyadmin"),
        ("git", "Git", "git", "Tools", "fa-code-branch", "/usr/bin/git", "https://git-scm.com/doc", "/files"),
        ("composer", "Composer", "composer", "Tools", "fa-box", "/usr/bin/composer", "https://getcomposer.org/doc/", "/files"),
        ("certbot", "Certbot (Let's Encrypt)", "certbot", "Tools", "fa-lock", "/etc/letsencrypt", "https://eff-certbot.readthedocs.io/", "/websites"),
        ("htop", "htop", "htop", "Tools", "fa-chart-line", "/usr/bin/htop", "https://htop.dev/", "/services"),
        ("fail2ban", "Fail2ban", "fail2ban", "Tools", "fa-shield-halved", "/etc/fail2ban", "https://www.fail2ban.org/wiki/index.php/Main_Page", "/services"),
        ("unzip", "Unzip", "unzip", "Tools", "fa-file-zipper", "/usr/bin/unzip", "https://infozip.sourceforge.net/UnZip.html", "/files"),
        ("postgresql", "PostgreSQL", "postgresql", "Deployment", "fa-database", "/var/lib/postgresql", "https://www.postgresql.org/docs/", "/services"),
        ("sqlite", "SQLite", "sqlite3", "Tools", "fa-database", "/usr/bin/sqlite3", "https://www.sqlite.org/docs.html", "/files"),
        ("supervisor", "Supervisor", "supervisor", "Deployment", "fa-gears", "/etc/supervisor", "https://supervisord.org/", "/services"),
        ("rabbitmq", "RabbitMQ", "rabbitmq-server", "Deployment", "fa-envelope", "/etc/rabbitmq", "https://www.rabbitmq.com/docs", "/services"),
        ("varnish", "Varnish Cache", "varnish", "Deployment", "fa-gauge-high", "/etc/varnish", "https://varnish-cache.org/docs/", "/services"),
        ("haproxy", "HAProxy", "haproxy", "Deployment", "fa-shuffle", "/etc/haproxy", "https://www.haproxy.org/#docs", "/services"),
        ("java", "OpenJDK Runtime", "default-jre-headless", "Deployment", "fa-coffee", "/usr/lib/jvm", "https://docs.oracle.com/en/java/", "/services"),
        ("go", "Go", "golang-go", "Deployment", "fa-code", "/usr/lib/go", "https://go.dev/doc/", "/services"),
        ("ruby", "Ruby", "ruby", "Deployment", "fa-gem", "/usr/bin/ruby", "https://www.ruby-lang.org/en/documentation/", "/services"),
        ("rsync", "Rsync", "rsync", "Tools", "fa-copy", "/usr/bin/rsync", "https://rsync.samba.org/documentation.html", "/files"),
        ("curl", "cURL", "curl", "Tools", "fa-globe", "/usr/bin/curl", "https://curl.se/docs/", "/files"),
        ("wget", "Wget", "wget", "Tools", "fa-download", "/usr/bin/wget", "https://www.gnu.org/software/wget/manual/", "/files"),
        ("nano", "Nano", "nano", "Tools", "fa-file-lines", "/usr/bin/nano", "https://www.nano-editor.org/docs.php", "/files"),
        ("vim", "Vim", "vim-tiny", "Tools", "fa-file-code", "/usr/bin/vim.tiny", "https://vimhelp.org/", "/files"),
        ("clamav", "ClamAV", "clamav", "Tools", "fa-shield-virus", "/etc/clamav", "https://docs.clamav.net/", "/services"),
    )
    versions_by_component = {
        "apache": options.get("apache", []),
        "mysql": options.get("mysql", []),
        "php": options.get("php", []),
        "phpmyadmin": options.get("phpmyadmin", []),
        "docker": docker_options.get("versions", []),
    }
    versions_by_component.update({
        component: options.get(component, [])
        for component in ADDITIONAL_PACKAGE_COMPONENTS
    })

    installed_versions: dict[str, str] = {}
    dpkg_query = shutil.which("dpkg-query")
    if dpkg_query:
        try:
            result = subprocess.run(
                [
                    dpkg_query,
                    "-W",
                    "-f=${binary:Package}\\t${Status}\\t${Version}\\n",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    fields = line.split("\t", 2)
                    if len(fields) != 3 or fields[1] != "install ok installed":
                        continue
                    package = fields[0].split(":", 1)[0]
                    installed_versions[package] = fields[2]
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Could not list installed Debian packages: %s", exc)

    apps = []
    for component, name, package, category, icon, location, official_url, config_url in app_definitions:
        versions = versions_by_component[component]
        if component == "docker" and not docker_options.get("supported"):
            versions = []
        if component == "php":
            installed_php = {
                version: installed_versions[f"php{version}-fpm"]
                for version in versions
                if f"php{version}-fpm" in installed_versions
            }
            installed = bool(installed_php)
            installed_version = ", ".join(sorted(installed_php))
        else:
            installed = package in installed_versions
            installed_version = installed_versions.get(package, "")
        update_version = (
            versions[0]
            if installed
            and component != "php"
            and versions
            and _version_is_newer(versions[0], installed_version)
            else ""
        )
        apps.append({
            "component": component,
            "name": name,
            "developer": "OS repository",
            "category": category,
            "icon": icon,
            "location": location,
            "official_url": official_url,
            "config_url": config_url,
            "versions": versions,
            "installed": installed,
            "installed_version": installed_version,
            "update_available": bool(update_version),
            "update_version": update_version,
            "available": bool(versions),
        })
    return apps


def validate_install_request(component: str, version: str) -> str | None:
    if component not in COMPONENTS:
        return "Select a supported app from the App Store."
    if component == "docker":
        docker_options = get_docker_options()
        if not docker_options["supported"]:
            return "The Docker installer supports Debian and Ubuntu only."
        if version not in docker_options["versions"]:
            return "Select an available Docker Engine version from the configured apt repositories."
        return None
    options = get_stack_options()
    if not options["supported"]:
        return "The installer supports Debian and Ubuntu only."
    if version not in options.get(component, []):
        return f"Select an available {component.upper()} version from the configured apt repositories."
    return None


def _append_output(job_id: str, chunk: str) -> None:
    with _job_lock:
        if _job["id"] == job_id:
            _job["output"] = (_job["output"] + chunk)[-24000:]


def _run_install(job_id: str, component: str, version: str) -> None:
    try:
        process = subprocess.Popen(
            ["bash", str(SCRIPT_PATH), "--component", component, "--version", version],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        if process.stdout is None:
            raise RuntimeError("Could not read installer output.")
        timeout = threading.Timer(1800, _kill_install_process, args=(process,))
        timeout.start()
        try:
            for line in process.stdout:
                _append_output(job_id, line)
            return_code = process.wait()
        finally:
            timeout.cancel()
        with _job_lock:
            if _job["id"] == job_id:
                _job["status"] = "completed" if return_code == 0 else "failed"
                if return_code != 0 and not _job["output"].endswith("\n"):
                    _job["output"] += "\n"
                if return_code != 0:
                    _job["output"] += f"Installer exited with status {return_code}.\n"
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.exception("Component installation failed")
        with _job_lock:
            if _job["id"] == job_id:
                _job["status"] = "failed"
                _job["output"] = (_job["output"] + f"\nInstaller error: {exc}\n")[-24000:]
    except (RuntimeError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
        logger.exception("Unexpected component installation failure")
        with _job_lock:
            if _job["id"] == job_id:
                _job["status"] = "failed"
                _job["output"] = (_job["output"] + f"\nInstaller error: {exc}\n")[-24000:]


def start_install(
    component: str, version: str, owner: str
) -> tuple[bool, str]:
    with _job_lock:
        if _job["status"] == "running":
            return False, "Another package installation is already in progress."
        job_id = uuid.uuid4().hex
        _job.update({
            "id": job_id,
            "owner": owner,
            "component": component,
            "version": version,
            "status": "running",
            "output": f"Installing {component.upper()} {version}...\n",
        })
    thread = threading.Thread(
        target=_run_install,
        args=(job_id, component, version),
        daemon=True,
    )
    try:
        thread.start()
    except RuntimeError as exc:
        with _job_lock:
            if _job["id"] == job_id:
                _job["status"] = "failed"
                _job["output"] += f"Could not start installer: {exc}\n"
        return False, str(exc)
    return True, job_id


def get_install_status(owner: str, job_id: str = "") -> dict:
    with _job_lock:
        if _job["owner"] != owner or (job_id and _job["id"] != job_id):
            return {"id": "", "status": "idle", "output": ""}
        return {
            key: value
            for key, value in _job.items()
            if key != "owner"
        }
