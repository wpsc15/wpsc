import json
import os
import re
import shutil
import subprocess

CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$", re.IGNORECASE)
MAX_CONTAINER_OUTPUT = 1_000_000
MAX_CONTAINER_LOG_BYTES = 131_072


def _run_docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )


def _require_docker() -> None:
    if not shutil.which("docker"):
        raise RuntimeError("Docker CLI is not installed on this server.")


def control_docker_service(action: str) -> tuple[bool, str]:
    if action not in {"start", "restart", "stop"}:
        return False, "Unsupported Docker service action."
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False, "Run WebPanel as root to manage the Docker service."
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False, "systemctl is not available on this server."
    try:
        result = subprocess.run(
            [systemctl, action, "docker"],
            capture_output=True,
            check=False,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Could not {action} the Docker service: {exc}"
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        return False, message or f"Could not {action} the Docker service."
    return True, f"Docker service {action} completed."


def list_containers() -> list[dict[str, str]]:
    _require_docker()
    try:
        result = _run_docker(
            "ps",
            "--all",
            "--last",
            "100",
            "--no-trunc",
            "--format",
            "{{json .}}",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not list Docker containers: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(message or "Docker could not list containers.")
    if len(result.stdout) > MAX_CONTAINER_OUTPUT:
        raise RuntimeError("Docker container list exceeded the output size limit.")

    containers = []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Docker returned invalid container data.") from exc
        if not isinstance(item, dict):
            raise RuntimeError("Docker returned invalid container data.")
        container_id = str(item.get("ID", ""))
        if not CONTAINER_ID.fullmatch(container_id):
            raise RuntimeError("Docker returned an invalid container ID.")
        containers.append(
            {
                "id": container_id.lower(),
                "name": str(item.get("Names", ""))[:200],
                "image": str(item.get("Image", ""))[:300],
                "status": str(item.get("Status", ""))[:300],
                "ports": str(item.get("Ports", ""))[:500],
            }
        )
    return containers


def _validate_container(container_id: str) -> str:
    if not CONTAINER_ID.fullmatch(container_id):
        raise ValueError("Select a valid Docker container.")
    installed = list_containers()
    if container_id.lower() not in {container["id"] for container in installed}:
        raise ValueError("Container was not found in the supported container list.")
    return container_id.lower()


def control_container(container_id: str, action: str) -> tuple[bool, str]:
    if action not in {"start", "stop", "restart"}:
        return False, "Unsupported container action."
    try:
        container_id = _validate_container(container_id)
    except (OSError, RuntimeError, ValueError) as exc:
        return False, str(exc)
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False, "Run WebPanel as root to manage Docker containers."

    try:
        result = _run_docker(action, container_id)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Could not {action} container: {exc}"
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        return False, message or f"Docker could not {action} the container."
    return True, f"Container {container_id[:12]} {action} completed."


def read_container_logs(container_id: str) -> str:
    container_id = _validate_container(container_id)
    try:
        result = _run_docker("logs", "--tail", "200", container_id)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not read container logs: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(message or "Docker could not read container logs.")
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    encoded = output.encode("utf-8", errors="replace")
    if len(encoded) > MAX_CONTAINER_LOG_BYTES:
        output = encoded[-MAX_CONTAINER_LOG_BYTES:].decode("utf-8", errors="replace")
        output = "[Earlier log output omitted to fit the 128 KiB limit.]\n" + output
    return output
