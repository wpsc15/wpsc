import subprocess
import psutil
import os
import platform
from typing import Tuple


def run_cmd(cmd: str, timeout: int = 30) -> Tuple[int, str, str]:
    """Run a shell command, return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)


def service_status(service: str) -> str:
    """Returns 'active', 'inactive', 'failed', 'activating', 'deactivating', or 'unknown'."""
    code, out, _ = run_cmd(f"systemctl is-active {service} 2>/dev/null")
    valid = ["active", "inactive", "failed", "activating", "deactivating"]
    return out if out in valid else "unknown"


def service_action(service: str, action: str) -> Tuple[bool, str]:
    """Execute start/stop/restart/reload via systemctl. Returns (success, message)."""
    code, out, err = run_cmd(f"systemctl {action} {service}")
    return code == 0, out or err


def get_system_info() -> dict:
    """Gather live CPU, RAM, disk, network, and load stats."""
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    try:
        load = list(os.getloadavg())
    except AttributeError:
        # Windows fallback — getloadavg not available
        load = [0.0, 0.0, 0.0]
    return {
        "cpu_percent": cpu,
        "ram_total": ram.total,
        "ram_used": ram.used,
        "ram_percent": ram.percent,
        "ram_free": ram.free,
        "disk_total": disk.total,
        "disk_used": disk.used,
        "disk_percent": disk.percent,
        "disk_free": disk.free,
        "net_sent": net.bytes_sent,
        "net_recv": net.bytes_recv,
        "load_1": round(load[0], 2),
        "load_5": round(load[1], 2),
        "load_15": round(load[2], 2),
        "uptime": int(psutil.boot_time()),
        "platform": platform.system(),
        "hostname": platform.node(),
    }


def bytes_to_human(n: int) -> str:
    """Convert bytes to a human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
