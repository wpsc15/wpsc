import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt

CRON_MARKER = re.compile(r"^# webpanel-job:([0-9a-f-]{36})$")
FIELD_TOKEN = re.compile(r"^(\*|\d+(?:-\d+)?)(?:/(\d+))?$")
CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
LOCK_PATH = Path("/run/lock/webpanel-cron.lock")
MAX_COMMAND_LENGTH = 1000


def _validate_field(field: str, minimum: int, maximum: int) -> bool:
    if not field or len(field) > 64:
        return False
    for token in field.split(","):
        match = FIELD_TOKEN.fullmatch(token)
        if not match:
            return False
        base, step = match.groups()
        if step is not None and (int(step) < 1 or int(step) > maximum - minimum + 1):
            return False
        if base == "*":
            continue
        values = base.split("-")
        if any(not value.isdigit() for value in values):
            return False
        numbers = [int(value) for value in values]
        if any(value < minimum or value > maximum for value in numbers):
            return False
        if len(numbers) == 2 and numbers[0] > numbers[1]:
            return False
    return True


def validate_schedule(schedule: str) -> str:
    fields = schedule.split()
    if len(schedule) > 64 or len(fields) != 5 or any(
        not _validate_field(field, minimum, maximum)
        for field, (minimum, maximum) in zip(fields, CRON_RANGES)
    ):
        raise ValueError("Use a valid five-field cron schedule, such as '0 2 * * *'.")
    return " ".join(fields)


def _read_crontab() -> str:
    if not shutil.which("crontab"):
        raise RuntimeError("The crontab command is not installed.")
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not read the root crontab: {exc}") from exc
    if result.returncode == 0:
        return result.stdout
    if "no crontab for " in result.stderr.lower():
        return ""
    raise RuntimeError(result.stderr.strip() or "Could not read the root crontab.")


def _write_crontab(contents: str) -> None:
    try:
        result = subprocess.run(
            ["crontab", "-"],
            input=contents,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not update the root crontab: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not update the root crontab.")


def _locked_crontab():
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise PermissionError("Run WebPanel as root to manage scheduled jobs.")
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = LOCK_PATH.open("a+")
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    else:
        lock_file.seek(0)
        if lock_file.read(1) == "":
            lock_file.write("\0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
    return lock_file


def _unlock(lock_file) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    else:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def list_managed_jobs() -> list[dict[str, str]]:
    with _locked_crontab() as lock_file:
        try:
            lines = _read_crontab().splitlines()
        finally:
            _unlock(lock_file)

    jobs = []
    index = 0
    while index < len(lines):
        marker = CRON_MARKER.fullmatch(lines[index])
        if marker:
            if index + 1 >= len(lines) or lines[index + 1].startswith("#"):
                raise RuntimeError("A WebPanel cron entry is malformed; inspect the root crontab.")
            entry = lines[index + 1].split(maxsplit=5)
            if len(entry) != 6:
                raise RuntimeError("A WebPanel cron entry is malformed; inspect the root crontab.")
            jobs.append(
                {
                    "id": marker.group(1),
                    "schedule": " ".join(entry[:5]),
                    "command": entry[5],
                }
            )
            index += 2
        else:
            index += 1
    return jobs


def add_managed_job(schedule: str, command: str) -> str:
    normalized_schedule = validate_schedule(schedule)
    if (
        not command
        or len(command) > MAX_COMMAND_LENGTH
        or any(ord(char) < 32 or ord(char) == 127 for char in command)
    ):
        raise ValueError("Enter a single-line command of 1 to 1000 printable characters.")
    if command.startswith("-"):
        raise ValueError("Cron commands cannot start with a hyphen.")

    with _locked_crontab() as lock_file:
        try:
            contents = _read_crontab()
            job_id = str(uuid.uuid4())
            separator = "" if not contents or contents.endswith("\n") else "\n"
            updated = (
                f"{contents}{separator}# webpanel-job:{job_id}\n"
                f"{normalized_schedule} {command}\n"
            )
            _write_crontab(updated)
            return job_id
        finally:
            _unlock(lock_file)


def remove_managed_job(job_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f-]{36}", job_id):
        raise ValueError("Select a valid WebPanel job.")
    with _locked_crontab() as lock_file:
        try:
            lines = _read_crontab().splitlines()
            kept = []
            found = False
            index = 0
            while index < len(lines):
                marker = CRON_MARKER.fullmatch(lines[index])
                if marker and marker.group(1) == job_id:
                    found = True
                    index += 2
                    continue
                kept.append(lines[index])
                index += 1
            if not found:
                raise ValueError("WebPanel job not found.")
            contents = "\n".join(kept)
            if contents:
                contents += "\n"
            _write_crontab(contents)
        finally:
            _unlock(lock_file)
