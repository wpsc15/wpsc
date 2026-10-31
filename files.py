from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from urllib.parse import urlencode
from urllib.parse import quote
from starlette.background import BackgroundTask

from database import log_action
from services.file_manager import (
    MAX_UPLOAD_SIZE,
    WEB_ROOT,
    delete_path,
    list_directory,
    open_download,
    read_text_file,
    save_text_file,
    save_upload,
    create_directory,
)

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
router = APIRouter(prefix="/files")


def _flash(request: Request, message: str, success: bool = False) -> None:
    request.session["flash"] = {
        "type": "success" if success else "error",
        "msg": message,
    }


def _files_url(path: str) -> str:
    return f"/files?{urlencode({'path': path})}"


@router.get("", response_class=HTMLResponse)
async def files_page(request: Request, path: str = "", edit: str = ""):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    current = ""
    entries = []
    content = None
    try:
        current, entries = list_directory(path)
        if edit and str(Path(edit).parent).replace("\\", "/") != current:
            raise ValueError("The file must be inside the current directory.")
        content = read_text_file(edit) if edit else None
    except (ValueError, OSError) as exc:
        _flash(request, str(exc))

    return templates.TemplateResponse(
        "files.html",
        {
            "request": request,
            "user": user,
            "active": "files",
            "flash": request.session.pop("flash", None),
            "path": current,
            "web_root": str(WEB_ROOT),
            "entries": entries,
            "edit_path": edit if content is not None else "",
            "content": content,
        },
    )


@router.post("/mkdir")
async def make_directory(
    request: Request, path: str = Form(""), name: str = Form(...)
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        create_directory(path, name)
        _flash(request, "Directory created.", True)
        log_action(
            user,
            "FILE_MKDIR",
            f"{path}/{name}",
            request.client.host if request.client else "unknown",
        )
    except (ValueError, OSError) as exc:
        _flash(request, str(exc))
    return RedirectResponse(_files_url(path), status_code=302)


@router.post("/save")
async def save_file(
    request: Request,
    path: str = Form(...),
    content: str = Form(""),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        save_text_file(path, content)
        _flash(request, "File saved.", True)
        log_action(
            user,
            "FILE_SAVE",
            path,
            request.client.host if request.client else "unknown",
        )
        parent = str(Path(path).parent).replace("\\", "/")
        return RedirectResponse(_files_url("" if parent == "." else parent), status_code=302)
    except (ValueError, OSError) as exc:
        _flash(request, str(exc))
        return RedirectResponse(_files_url(str(Path(path).parent)), status_code=302)


@router.post("/upload")
async def upload_file(
    request: Request,
    path: str = Form(""),
    upload: UploadFile = File(...),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        content = await upload.read(MAX_UPLOAD_SIZE + 1)
        save_upload(path, upload.filename or "", content)
        _flash(request, "File uploaded.", True)
        log_action(
            user,
            "FILE_UPLOAD",
            f"{path}/{upload.filename}",
            request.client.host if request.client else "unknown",
        )
    except (ValueError, OSError) as exc:
        _flash(request, str(exc))
    finally:
        await upload.close()
    return RedirectResponse(_files_url(path), status_code=302)


@router.post("/delete")
async def remove_file(request: Request, path: str = Form(...)):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=302)
    try:
        delete_path(path)
        _flash(request, "File or empty directory deleted.", True)
        log_action(
            user,
            "FILE_DELETE",
            path,
            request.client.host if request.client else "unknown",
        )
    except (ValueError, OSError) as exc:
        _flash(request, str(exc))
    parent = str(Path(path).parent).replace("\\", "/")
    return RedirectResponse(_files_url("" if parent == "." else parent), status_code=302)


@router.get("/download")
async def download_file(request: Request, path: str):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=302)
    try:
        source = open_download(path)
        filename = Path(path).name
        return StreamingResponse(
            iter(lambda: source.read(64 * 1024), b""),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
            },
            background=BackgroundTask(source.close),
        )
    except (ValueError, OSError) as exc:
        _flash(request, str(exc))
        return RedirectResponse("/files", status_code=302)
