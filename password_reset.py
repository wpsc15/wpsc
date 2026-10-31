import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote, urlsplit


def build_reset_url(token: str) -> str:
    public_url = os.environ.get("WEBPANEL_PUBLIC_URL", "").strip().rstrip("/")
    parsed = urlsplit(public_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Set WEBPANEL_PUBLIC_URL to the panel's canonical HTTPS URL.")
    if parsed.query or parsed.fragment:
        raise ValueError("WEBPANEL_PUBLIC_URL must not include a query or fragment.")
    return f"{public_url}/reset-password?token={quote(token, safe='')}"


def send_password_reset_email(recipient: str, reset_url: str) -> None:
    host = os.environ.get("WEBPANEL_SMTP_HOST", "").strip()
    sender = os.environ.get("WEBPANEL_SMTP_FROM", "").strip()
    if not host or not sender:
        raise ValueError("Configure WEBPANEL_SMTP_HOST and WEBPANEL_SMTP_FROM to send recovery email.")

    try:
        port = int(os.environ.get("WEBPANEL_SMTP_PORT", "587"))
    except ValueError as exc:
        raise ValueError("WEBPANEL_SMTP_PORT must be a valid port number.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("WEBPANEL_SMTP_PORT must be between 1 and 65535.")

    username = os.environ.get("WEBPANEL_SMTP_USER", "").strip()
    password = os.environ.get("WEBPANEL_SMTP_PASSWORD", "")
    if bool(username) != bool(password):
        raise ValueError("Configure both WEBPANEL_SMTP_USER and WEBPANEL_SMTP_PASSWORD, or neither.")

    message = EmailMessage()
    message["Subject"] = "Reset your WebPanel password"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "A password reset was requested for your WebPanel account.\n\n"
        f"Use this link within 30 minutes to set a new password:\n{reset_url}\n\n"
        "If you did not request this, you can ignore this email."
    )

    timeout = 15
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context()) as server:
            if username:
                server.login(username, password)
            server.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=timeout) as server:
        server.ehlo()
        if os.environ.get("WEBPANEL_SMTP_STARTTLS", "true").strip().lower() not in {"0", "false", "no"}:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if username:
            server.login(username, password)
        server.send_message(message)
