import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import BinaryIO


BACKUP_NAME_PATTERN = re.compile(r"^mysql-\d{8}-\d{6}-\d{6}\.sql$")
DATABASE_BACKUP_NAME_PATTERN = re.compile(
    r"^mysql-db-[A-Za-z0-9_$-]{1,64}-\d{8}-\d{6}-\d{6}\.sql$"
)
MAX_RESTORE_SIZE = 256 * 1024 * 1024


def backup_directory(base_dir: Path) -> Path:
    directory = base_dir / "database_backups"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def create_backup(root_pass: str, base_dir: Path) -> Path:
    directory = backup_directory(base_dir)
    backup = directory / f"mysql-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.sql"
    temporary = backup.with_suffix(".sql.tmp")
    environment = os.environ.copy()
    if root_pass:
        environment["MYSQL_PWD"] = root_pass
    try:
        with temporary.open("wb") as output:
            result = subprocess.run(
                [
                    "mysqldump",
                    "--all-databases",
                    "--routines",
                    "--events",
                    "--triggers",
                    "--single-transaction",
                ],
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=900,
                env=environment,
                check=False,
            )
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or f"mysqldump exited with status {result.returncode}.")
        if temporary.stat().st_size == 0:
            raise RuntimeError("mysqldump produced an empty backup.")
        os.chmod(temporary, 0o600)
        temporary.replace(backup)
        return backup
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        temporary.unlink(missing_ok=True)
        raise


def create_database_backup(database: str, root_pass: str, base_dir: Path) -> Path:
    from services.mysql_svc import DATABASE_NAME_PATTERN

    if not DATABASE_NAME_PATTERN.fullmatch(database):
        raise ValueError("Invalid database name.")
    directory = backup_directory(base_dir)
    backup = directory / (
        f"mysql-db-{database}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.sql"
    )
    temporary = backup.with_suffix(".sql.tmp")
    environment = os.environ.copy()
    if root_pass:
        environment["MYSQL_PWD"] = root_pass
    try:
        with temporary.open("wb") as output:
            result = subprocess.run(
                [
                    "mysqldump",
                    "--single-transaction",
                    "--routines",
                    "--events",
                    "--triggers",
                    f"--databases={database}",
                ],
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=900,
                env=environment,
                check=False,
            )
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or f"mysqldump exited with status {result.returncode}.")
        if temporary.stat().st_size == 0:
            raise RuntimeError("mysqldump produced an empty database backup.")
        os.chmod(temporary, 0o600)
        temporary.replace(backup)
        return backup
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        temporary.unlink(missing_ok=True)
        raise


def list_backups(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (
            path for path in directory.iterdir()
            if BACKUP_NAME_PATTERN.fullmatch(path.name)
            and path.is_file()
            and not path.is_symlink()
        ),
        key=lambda path: path.name,
        reverse=True,
    )


def find_backup(directory: Path, filename: str) -> Path:
    if not BACKUP_NAME_PATTERN.fullmatch(filename):
        raise FileNotFoundError("Backup file was not found.")
    path = directory / filename
    if path.is_symlink() or not path.is_file() or path.parent.resolve() != directory.resolve():
        raise FileNotFoundError("Backup file was not found.")
    return path


def find_database_backup(directory: Path, filename: str) -> Path:
    if not DATABASE_BACKUP_NAME_PATTERN.fullmatch(filename):
        raise FileNotFoundError("Database backup was not found.")
    path = directory / filename
    if path.is_symlink() or not path.is_file() or path.parent.resolve() != directory.resolve():
        raise FileNotFoundError("Database backup was not found.")
    return path


def list_database_backups(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (
            path
            for path in directory.iterdir()
            if DATABASE_BACKUP_NAME_PATTERN.fullmatch(path.name)
            and path.is_file()
            and not path.is_symlink()
        ),
        key=lambda path: path.name,
        reverse=True,
    )


def restore_backup(root_pass: str, source: BinaryIO) -> tuple[bool, str]:
    environment = os.environ.copy()
    if root_pass:
        environment["MYSQL_PWD"] = root_pass
    try:
        result = subprocess.run(
            ["mysql", "--binary-mode"],
            stdin=source,
            capture_output=True,
            text=True,
            timeout=1800,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return result.returncode == 0, result.stderr.strip() or result.stdout.strip()
