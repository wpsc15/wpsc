import os
import re
import shutil
import subprocess
import threading

from services.domains import validate_domain

_CERTBOT_LOCK = threading.Lock()


def issue_apache_certificate(domain: str, email: str) -> None:
    """Obtain and install an HTTP-validated Let's Encrypt certificate in Apache."""
    domain = validate_domain(domain)
    email = email.strip()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Enter a valid email address for certificate expiry notices.")
    if getattr(os, "geteuid", lambda: -1)() != 0:
        raise PermissionError("Run WebPanel as root to install an SSL certificate.")
    if not shutil.which("certbot"):
        raise RuntimeError(
            "Certbot is not installed. Install certbot and its Apache plugin, then try again."
        )
    if not _CERTBOT_LOCK.acquire(blocking=False):
        raise RuntimeError("Another Let's Encrypt certificate request is already running.")

    try:
        try:
            result = subprocess.run(
                [
                    "certbot",
                    "--apache",
                    "--non-interactive",
                    "--agree-tos",
                    "--redirect",
                    "--email",
                    email,
                    "--domains",
                    domain,
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Let's Encrypt request timed out. Check DNS, port 80 reachability, and the Certbot log."
            ) from exc

        if result.returncode:
            details = (result.stderr or result.stdout).strip()
            if details:
                details = details[-1600:]
                raise RuntimeError(f"Let's Encrypt could not install the certificate: {details}")
            raise RuntimeError("Let's Encrypt could not install the certificate.")
    finally:
        _CERTBOT_LOCK.release()
