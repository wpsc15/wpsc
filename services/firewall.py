import os
import re
import shutil
import subprocess

PORT_RE = re.compile(r"^\d{1,5}$")
PROTOCOLS = {"tcp", "udp"}
RULE_ACTIONS = {"allow", "deny"}
MAX_RULES_OUTPUT = 12000


def _run_ufw(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ufw", *args],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )


def get_firewall_status() -> dict[str, str]:
    if not shutil.which("ufw"):
        return {
            "available": "false",
            "status": "not installed",
            "rules": "",
            "error": "UFW is not installed. Install and configure UFW on the server to manage firewall rules here.",
        }
    try:
        result = _run_ufw("status", "numbered")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": "true", "status": "error", "rules": "", "error": str(exc)}
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        return {
            "available": "true",
            "status": "error",
            "rules": "",
            "error": message or "Could not read UFW status.",
        }
    output = result.stdout.strip()
    status = "active" if re.search(r"^Status:\s*active$", output, re.MULTILINE) else "inactive"
    return {
        "available": "true",
        "status": status,
        "rules": output[:MAX_RULES_OUTPUT],
        "error": "",
    }


def set_firewall_state(action: str) -> tuple[bool, str]:
    if action not in {"enable", "disable"}:
        return False, "Unsupported firewall action."
    if not shutil.which("ufw"):
        return False, "UFW is not installed."
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False, "Run WebPanel as root to manage the firewall."
    if action == "disable":
        try:
            status = _run_ufw("status")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"Could not read firewall status: {exc}"
        if status.returncode != 0:
            return False, status.stderr.strip() or "Could not read firewall status."
        if re.search(r"^Status:\s*active$", status.stdout, re.MULTILINE):
            return False, "Disabling the firewall is blocked from the panel. Use a trusted local console if necessary."
        return True, "UFW is already inactive."
    try:
        result = _run_ufw("--force", "enable")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Could not enable UFW: {exc}"
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        return False, message or "Could not enable UFW."
    return True, "UFW is enabled."


def change_firewall_rule(action: str, port: str, protocol: str) -> tuple[bool, str]:
    if action not in RULE_ACTIONS:
        return False, "Choose allow or deny."
    if not PORT_RE.fullmatch(port) or not 1 <= int(port) <= 65535:
        return False, "Enter a port between 1 and 65535."
    protocol = protocol.lower()
    if protocol not in PROTOCOLS:
        return False, "Choose TCP or UDP."
    if not shutil.which("ufw"):
        return False, "UFW is not installed."
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False, "Run WebPanel as root to manage firewall rules."

    try:
        result = _run_ufw(action, f"{int(port)}/{protocol}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Could not update the firewall rule: {exc}"
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        return False, message or "Could not update the firewall rule."
    return True, f"UFW rule '{action} {int(port)}/{protocol}' applied."


def remove_firewall_rule(action: str, port: str, protocol: str) -> tuple[bool, str]:
    if action not in RULE_ACTIONS:
        return False, "Choose the rule action to remove."
    if not PORT_RE.fullmatch(port) or not 1 <= int(port) <= 65535:
        return False, "Enter a port between 1 and 65535."
    protocol = protocol.lower()
    if protocol not in PROTOCOLS:
        return False, "Choose TCP or UDP."
    if not shutil.which("ufw"):
        return False, "UFW is not installed."
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False, "Run WebPanel as root to manage firewall rules."

    try:
        result = _run_ufw("delete", action, f"{int(port)}/{protocol}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Could not remove the firewall rule: {exc}"
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        return False, message or "Could not remove the firewall rule."
    return True, f"UFW rule '{action} {int(port)}/{protocol}' removed."
