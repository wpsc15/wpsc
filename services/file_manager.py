import os
import stat
from pathlib import Path, PurePosixPath
from typing import BinaryIO


WEB_ROOT = Path(os.environ.get("WEBPANEL_WEB_ROOT", "/var/www")).expanduser()
MAX_EDIT_SIZE = 1024 * 1024
MAX_UPLOAD_SIZE = 10 * 1024 * 1024


def _supports_safe_file_descriptors() -> bool:
    return (
        os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
    )


def _path_parts(relative_path: str) -> tuple[Path, tuple[str, ...]]:
    if "\\" in relative_path or relative_path.startswith("/"):
        raise ValueError("Paths must be relative to /var/www.")
    parts = PurePosixPath(relative_path or ".").parts
    if any(part in {"..", ""} for part in parts):
        raise ValueError("Path traversal is not allowed.")
    return WEB_ROOT.resolve(), tuple(part for part in parts if part != ".")


def _open_directory_fd(relative_path: str = "") -> int:
    root, parts = _path_parts(relative_path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        for part in parts:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_parent_fd(relative_path: str) -> tuple[int, str]:
    _, parts = _path_parts(relative_path)
    if not parts:
        raise ValueError("The web root is not a file.")
    parent_path = "/".join(parts[:-1])
    return _open_directory_fd(parent_path), parts[-1]


def _open_file_descriptor(relative_path: str, flags: int) -> int:
    parent_fd, filename = _open_parent_fd(relative_path)
    try:
        descriptor = os.open(
            filename,
            flags | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ValueError("Only regular files are accessible.")
    if metadata.st_nlink > 1:
        os.close(descriptor)
        raise ValueError("Hard-linked files are not accessible.")
    return descriptor


def _write_all(descriptor: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(descriptor, remaining)
        if written == 0:
            raise OSError("File write made no progress.")
        remaining = remaining[written:]


def resolve_web_path(relative_path: str = "") -> Path:
    """Resolve a path under /var/www and reject traversal and symlinks."""
    root, parts = _path_parts(relative_path)
    target = root.joinpath(*parts)
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symbolic links are not accessible.")

    resolved = target.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("Path must remain inside /var/www.")
    if target.exists():
        metadata = target.lstat()
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1:
            raise ValueError("Hard-linked files are not accessible.")
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
            raise ValueError("Special filesystem entries are not accessible.")
    return target


def list_directory(relative_path: str = "") -> tuple[str, list[dict]]:
    entries = []
    directory = resolve_web_path(relative_path)
    if _supports_safe_file_descriptors():
        directory_fd = _open_directory_fd(relative_path)
        try:
            with os.scandir(os.dup(directory_fd)) as items:
                for item in items:
                    file_stat = item.stat(follow_symlinks=False)
                    entries.append({
                        "name": item.name,
                        "is_dir": item.is_dir(follow_symlinks=False),
                        "is_file": item.is_file(follow_symlinks=False),
                        "is_symlink": item.is_symlink(),
                        "is_hardlink": stat.S_ISREG(file_stat.st_mode) and file_stat.st_nlink > 1,
                        "size": file_stat.st_size,
                        "modified": file_stat.st_mtime,
                    })
        finally:
            os.close(directory_fd)
    else:
        if not directory.exists() or not directory.is_dir():
            raise FileNotFoundError("Directory not found.")
        with os.scandir(directory) as items:
            for item in items:
                file_stat = item.stat(follow_symlinks=False)
                entries.append({
                    "name": item.name,
                    "is_dir": item.is_dir(follow_symlinks=False),
                    "is_file": item.is_file(follow_symlinks=False),
                    "is_symlink": item.is_symlink(),
                    "is_hardlink": stat.S_ISREG(file_stat.st_mode) and file_stat.st_nlink > 1,
                    "size": file_stat.st_size,
                    "modified": file_stat.st_mtime,
                })
    entries.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    current = directory.relative_to(WEB_ROOT.resolve()).as_posix()
    return ("" if current == "." else current), entries


def read_text_file(relative_path: str) -> str:
    try:
        if _supports_safe_file_descriptors():
            descriptor = _open_file_descriptor(relative_path, os.O_RDONLY)
            with os.fdopen(descriptor, "rb") as source:
                content = source.read(MAX_EDIT_SIZE + 1)
        else:
            target = resolve_web_path(relative_path)
            if not target.is_file():
                raise FileNotFoundError("File not found.")
            if target.stat().st_size > MAX_EDIT_SIZE:
                raise ValueError("Files larger than 1 MiB cannot be opened in the editor.")
            content = target.read_bytes()
        if len(content) > MAX_EDIT_SIZE:
            raise ValueError("Files larger than 1 MiB cannot be opened in the editor.")
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("This file is not UTF-8 text and cannot be edited here.") from exc


def save_text_file(relative_path: str, content: str) -> None:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_EDIT_SIZE:
        raise ValueError("Edited files are limited to 1 MiB.")
    if _supports_safe_file_descriptors():
        parent_fd, filename = _open_parent_fd(relative_path)
        try:
            try:
                existing = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing and (
                not stat.S_ISREG(existing.st_mode) or existing.st_nlink > 1
            ):
                raise ValueError("The target is not an unlinked regular file.")
            descriptor = os.open(
                filename,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_NOFOLLOW
                | getattr(os, "O_NONBLOCK", 0),
                0o644,
                dir_fd=parent_fd,
            )
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink > 1:
                    raise ValueError("The target is not an unlinked regular file.")
                os.ftruncate(descriptor, 0)
                _write_all(descriptor, encoded)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent_fd)
    else:
        target = resolve_web_path(relative_path)
        if not target.parent.is_dir():
            raise FileNotFoundError("Parent directory not found.")
        if target.exists() and not target.is_file():
            raise ValueError("The target is not a regular file.")
        target.write_bytes(encoded)


def create_directory(parent: str, name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Enter a single valid directory name.")
    relative = f"{parent}/{name}" if parent else name
    if _supports_safe_file_descriptors():
        parent_fd, directory_name = _open_parent_fd(relative)
        try:
            os.mkdir(directory_name, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
    else:
        resolve_web_path(relative).mkdir()


def save_upload(relative_path: str, filename: str, content: bytes) -> None:
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or len(filename) > 255
    ):
        raise ValueError("Enter a valid file name.")
    if len(content) > MAX_UPLOAD_SIZE:
        raise ValueError("Uploads are limited to 10 MiB.")
    relative = f"{relative_path}/{filename}" if relative_path else filename
    if _supports_safe_file_descriptors():
        parent_fd, target_name = _open_parent_fd(relative)
        try:
            descriptor = os.open(
                target_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | getattr(os, "O_NONBLOCK", 0),
                0o644,
                dir_fd=parent_fd,
            )
            try:
                _write_all(descriptor, content)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent_fd)
    else:
        target = resolve_web_path(relative)
        if target.exists():
            raise ValueError("A file with that name already exists.")
        if not target.parent.is_dir():
            raise FileNotFoundError("Parent directory not found.")
        target.write_bytes(content)


def delete_path(relative_path: str) -> None:
    if _supports_safe_file_descriptors():
        parent_fd, target_name = _open_parent_fd(relative_path)
        try:
            metadata = os.stat(
                target_name, dir_fd=parent_fd, follow_symlinks=False
            )
            if stat.S_ISDIR(metadata.st_mode):
                os.rmdir(target_name, dir_fd=parent_fd)
            elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                os.unlink(target_name, dir_fd=parent_fd)
            else:
                raise ValueError("Only regular files and empty directories can be deleted.")
        finally:
            os.close(parent_fd)
    else:
        target = resolve_web_path(relative_path)
        if target == WEB_ROOT.resolve():
            raise ValueError("The web root cannot be deleted.")
        if target.is_dir():
            target.rmdir()
        else:
            target.unlink()


def open_download(relative_path: str) -> BinaryIO:
    if _supports_safe_file_descriptors():
        descriptor = _open_file_descriptor(relative_path, os.O_RDONLY)
        return os.fdopen(descriptor, "rb")
    target = resolve_web_path(relative_path)
    if not target.is_file():
        raise FileNotFoundError("File not found.")
    return target.open("rb")
