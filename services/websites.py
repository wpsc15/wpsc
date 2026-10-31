import concurrent.futures
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from database import get_db
from services.domains import validate_domain


def create_apache_vhost(domain: str, php_version: str = "") -> dict:
    """Create and enable a safe Debian/Ubuntu Apache vhost for a domain."""
    domain = validate_domain(domain)
    available_dir = Path("/etc/apache2/sites-available")
    enabled_dir = Path("/etc/apache2/sites-enabled")
    web_root = Path("/var/www")
    config_path = available_dir / f"{domain}.conf"
    document_root = web_root / domain

    if getattr(os, "geteuid", lambda: -1)() != 0:
        raise PermissionError("Run WebPanel as root to create Apache virtual hosts.")
    if not available_dir.is_dir() or not enabled_dir.is_dir():
        raise RuntimeError("Debian/Ubuntu Apache sites directories were not found.")
    if not web_root.is_dir() or web_root.is_symlink():
        raise RuntimeError("/var/www must exist and must not be a symbolic link.")
    if config_path.exists() or config_path.is_symlink():
        raise FileExistsError(f"Apache configuration already exists: {config_path}")
    if document_root.is_symlink():
        raise ValueError("The website document root cannot be a symbolic link.")
    php_handler = ""
    if php_version:
        if not re.fullmatch(r"\d+\.\d+", php_version):
            raise ValueError("Select an installed PHP-FPM version.")
        socket_path = Path(f"/run/php/php{php_version}-fpm.sock")
        if not socket_path.exists() or socket_path.is_symlink():
            raise RuntimeError(f"PHP {php_version} FPM is not running or its socket is unavailable.")
        result = subprocess.run(
            ["a2enmod", "proxy_fcgi", "setenvif"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Could not enable Apache PHP-FPM modules.")
        php_handler = (
            "    <FilesMatch \\.php$>\n"
            f'        SetHandler "proxy:unix:{socket_path}|fcgi://localhost/"\n'
            "    </FilesMatch>\n"
        )

    root_created = False
    index_created = False
    config_created = False
    site_enabled = False
    try:
        try:
            document_root.mkdir(mode=0o755)
            root_created = True
        except FileExistsError:
            if not document_root.is_dir():
                raise ValueError("The domain document root is not a directory.")

        index_path = document_root / "index.html"
        if not index_path.exists():
            with index_path.open("x", encoding="utf-8") as index:
                index.write(f"<!doctype html><title>{domain}</title><h1>{domain}</h1>\n")
            index_created = True

        alias = "" if domain == "www" or domain.startswith("www.") else f"    ServerAlias www.{domain}\n"
        config = (
            "<VirtualHost *:80>\n"
            f"    ServerName {domain}\n"
            f"{alias}"
            f"    DocumentRoot {document_root}\n"
            f"    <Directory {document_root}>\n"
            "        Options -Indexes\n"
            "        AllowOverride All\n"
            "        Require all granted\n"
            "    </Directory>\n"
            f"{php_handler}"
            f"    ErrorLog ${{APACHE_LOG_DIR}}/{domain}-error.log\n"
            f"    CustomLog ${{APACHE_LOG_DIR}}/{domain}-access.log combined\n"
            "</VirtualHost>\n"
        )
        with config_path.open("x", encoding="utf-8") as config_file:
            config_file.write(config)
        config_created = True

        result = subprocess.run(
            ["apache2ctl", "configtest"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Apache configtest failed.")

        result = subprocess.run(
            ["a2ensite", config_path.name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Could not enable Apache site.")
        site_enabled = True

        result = subprocess.run(
            ["apache2ctl", "configtest"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Apache configtest failed.")
        result = subprocess.run(
            ["systemctl", "reload", "apache2"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Could not reload Apache.")

        return {
            "domain": domain,
            "vhost_path": str(config_path),
            "document_root": str(document_root),
        }
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        if site_enabled:
            subprocess.run(
                ["a2dissite", config_path.name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            subprocess.run(
                ["systemctl", "reload", "apache2"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        if config_created:
            config_path.unlink(missing_ok=True)
        if index_created:
            (document_root / "index.html").unlink(missing_ok=True)
        if root_created:
            document_root.rmdir()
        raise


def validate_website_url(url: str) -> str:
    """Normalize and validate a monitor URL."""
    value = url.strip()
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Enter a valid HTTP or HTTPS URL.") from exc
    if (
        len(value) > 2048
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Enter a valid HTTP or HTTPS URL.")
    return value


def list_websites() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM monitored_websites ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def add_website(
    name: str, url: str, vhost_path: str = "", document_root: str = ""
) -> int:
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 120:
        raise ValueError("Website name must be between 1 and 120 characters.")
    clean_url = validate_website_url(url)
    conn = get_db()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM monitored_websites"
        ).fetchone()[0]
        if count >= 100:
            raise ValueError("A maximum of 100 websites can be monitored.")
        cursor = conn.execute(
            """
            INSERT INTO monitored_websites (name, url, vhost_path, document_root)
            VALUES (?, ?, ?, ?)
            """,
            (clean_name, clean_url, vhost_path, document_root),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError as exc:
        raise ValueError("That URL is already being monitored.") from exc
    finally:
        conn.close()


def set_website_vhost(
    website_id: int, domain: str, vhost_path: str, document_root: str
) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE monitored_websites
            SET vhost_domain = ?, vhost_path = ?, document_root = ?
            WHERE id = ?
            """,
            (domain, vhost_path, document_root, website_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_website(website_id: int) -> bool:
    conn = get_db()
    try:
        cursor = conn.execute(
            "DELETE FROM monitored_websites WHERE id = ?", (website_id,)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def check_website(website_id: int, url: str) -> None:
    """Probe a URL and persist its result; network failures are recorded as down."""
    started = time.monotonic()
    status_code = None
    error = ""
    try:
        request = Request(url, headers={"User-Agent": "WebPanel-Monitor/1.0"})
        with urlopen(request, timeout=10) as response:
            status_code = response.status
    except HTTPError as exc:
        status_code = exc.code
        exc.close()
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        error = str(exc.reason if isinstance(exc, URLError) else exc)[:256]

    elapsed = round((time.monotonic() - started) * 1000)
    is_up = status_code is not None and 200 <= status_code < 400
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE monitored_websites
            SET status_code = ?, response_time_ms = ?, is_up = ?,
                last_checked = datetime('now'), error = ?
            WHERE id = ?
            """,
            (status_code, elapsed, int(is_up), error, website_id),
        )
        conn.commit()
    finally:
        conn.close()


def check_all_websites() -> None:
    websites = list_websites()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(check_website, site["id"], site["url"])
            for site in websites
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()


def discover_wordpress_sites(websites: list[dict], web_root: Path = Path("/var/www")) -> list[dict]:
    """Find WordPress installations under the web root without following symlinks."""
    if web_root.is_symlink() or not web_root.is_dir():
        return []

    try:
        canonical_root = web_root.resolve(strict=True)
    except OSError:
        return []

    site_by_root = {}
    for site in websites:
        if not site.get("document_root"):
            continue
        try:
            site_by_root[str(Path(site["document_root"]).resolve(strict=False))] = site
        except (OSError, ValueError):
            continue
    candidates: set[Path] = set()
    visited = 0
    for current, dirs, _ in os.walk(canonical_root, followlinks=False):
        current_path = Path(current)
        depth = len(current_path.relative_to(canonical_root).parts)
        dirs[:] = [
            name for name in dirs
            if not name.startswith(".") and not (current_path / name).is_symlink()
        ]
        if depth > 4 or visited >= 500:
            dirs[:] = []
            continue
        visited += 1
        if (current_path / "wp-config.php").is_file() and not (current_path / "wp-config.php").is_symlink():
            candidates.add(current_path)

    discovered = []
    for root in sorted(candidates):
        try:
            resolved_root = root.resolve(strict=True)
            if not resolved_root.is_relative_to(canonical_root):
                continue
            version_file = resolved_root / "wp-includes" / "version.php"
            if (resolved_root / "wp-includes").is_symlink() or version_file.is_symlink():
                continue
            version_text = version_file.read_text(encoding="utf-8", errors="replace")[:65536]
            match = re.search(r"\$wp_version\s*=\s*['\"]([^'\"]{1,32})['\"]", version_text)
            if not match:
                continue
            site = site_by_root.get(str(resolved_root), {})
            try:
                parsed_url = urlsplit(validate_website_url(site.get("url", "")))
            except ValueError:
                parsed_url = urlsplit("")
            domain = site.get("vhost_domain") or parsed_url.hostname or root.name
            origin = f"{parsed_url.scheme}://{parsed_url.netloc}" if parsed_url.scheme and parsed_url.netloc else ""
            discovered.append(
                {
                    "name": site.get("name") or domain,
                    "domain": domain,
                    "url": site.get("url") or origin,
                    "root": str(resolved_root),
                    "version": match.group(1),
                    "plugin_count": _count_wordpress_items(resolved_root / "wp-content" / "plugins"),
                    "theme_count": _count_wordpress_items(resolved_root / "wp-content" / "themes"),
                    "admin_url": f"{origin}/wp-admin/" if origin else "",
                    "monitored": bool(site),
                    "is_up": bool(site.get("is_up")),
                    "status_code": site.get("status_code"),
                    "response_time_ms": site.get("response_time_ms"),
                    "last_checked": site.get("last_checked"),
                }
            )
        except (OSError, ValueError):
            continue
    return discovered


def _count_wordpress_items(directory: Path) -> int:
    if directory.is_symlink() or not directory.is_dir():
        return 0
    try:
        return sum(
            1 for entry in directory.iterdir()
            if not entry.name.startswith(".") and entry.is_dir() and not entry.is_symlink()
        )
    except OSError:
        return 0
