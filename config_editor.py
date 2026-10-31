import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from collections.abc import Sequence


MAX_CONFIG_SIZE = 1_048_576


def read_config(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("Configuration file is missing or is not a regular file.")
    return path.read_text(encoding="utf-8")


def _atomic_write(path: Path, content: bytes, mode: int, uid: int, gid: int) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, mode)
        if os.name == "posix" and hasattr(os, "chown"):
            os.chown(temporary_path, uid, gid)
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def _run_command(command: Sequence[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    message = (result.stderr or result.stdout).strip()
    return result.returncode == 0, message or f"{command[0]} exited with status {result.returncode}."


def save_config(
    path: Path,
    content: str,
    validators: Sequence[Sequence[str]],
    apply_commands: Sequence[Sequence[str]] = (),
) -> tuple[bool, str]:
    if path.is_symlink() or not path.is_file():
        return False, "Configuration file is missing or is not a regular file."
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_CONFIG_SIZE:
        return False, "Configuration exceeds the 1 MiB editor limit."
    if b"\x00" in encoded:
        return False, "Configuration cannot contain NUL bytes."

    try:
        original = path.read_bytes()
        metadata = path.stat()
        backup = path.with_name(
            f"{path.name}.webpanel-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.bak"
        )
        shutil.copy2(path, backup)
        _atomic_write(path, encoded, metadata.st_mode, metadata.st_uid, metadata.st_gid)
    except (OSError, UnicodeError) as exc:
        return False, f"Could not save configuration: {exc}"

    for command in validators:
        ok, message = _run_command(command)
        if not ok:
            try:
                _atomic_write(path, original, metadata.st_mode, metadata.st_uid, metadata.st_gid)
            except OSError as exc:
                return False, f"Validation failed ({message}); automatic rollback also failed: {exc}. Backup: {backup}"
            return False, f"Validation failed ({message}); original restored. Backup: {backup}"

    for command in apply_commands:
        ok, message = _run_command(command)
        if not ok:
            try:
                _atomic_write(path, original, metadata.st_mode, metadata.st_uid, metadata.st_gid)
            except OSError as exc:
                return False, f"Applying configuration failed ({message}); rollback failed: {exc}. Backup: {backup}"
            recovery_errors = []
            for recovery_command in apply_commands:
                recovered, recovery_message = _run_command(recovery_command)
                if not recovered:
                    recovery_errors.append(recovery_message)
            if recovery_errors:
                return False, f"Applying configuration failed ({message}); original restored but service recovery failed: {'; '.join(recovery_errors)}. Backup: {backup}"
            return False, f"Applying configuration failed ({message}); original restored. Backup: {backup}"

    return True, f"Saved successfully. Backup: {backup}"
