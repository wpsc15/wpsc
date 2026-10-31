from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import logging
import uvicorn

from config import SECRET_KEY
from database import init_db
from routers import (
    apache,
    audit,
    auth,
    cron,
    dashboard,
    docker,
    files,
    firewall,
    logs,
    monitor,
    mysql_router,
    php_router,
    services,
    settings,
    stack,
    websites,
    wp_toolkit,
)
from services.websites import check_all_websites

logger = logging.getLogger(__name__)


async def monitor_websites():
    while True:
        try:
            await asyncio.to_thread(check_all_websites)
        except Exception:
            logger.exception("Website monitoring cycle failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database on startup."""
    init_db()
    monitor_task = asyncio.create_task(monitor_websites())
    print("=" * 50)
    print("  WebPanel started successfully!")
    print("  Visit: http://localhost:8080")
    print("  Default login: admin / admin123")
    print("  Change your password after first login!")
    print("=" * 50)
    try:
        yield
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="WebPanel",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,   # Disable Swagger UI
    redoc_url=None,  # Disable ReDoc
)

# Session middleware (cookie-based auth)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=86400,              # 24-hour session
    session_cookie="webpanel_session",
    https_only=False,           # Set True in production with HTTPS
)

# Static files (CSS, JS, images)
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Routers
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(apache.router)
app.include_router(mysql_router.router)
app.include_router(php_router.router)
app.include_router(monitor.router)
app.include_router(websites.router)
app.include_router(wp_toolkit.router)
app.include_router(files.router)
app.include_router(stack.router)
app.include_router(services.router)
app.include_router(logs.router)
app.include_router(cron.router)
app.include_router(firewall.router)
app.include_router(docker.router)
app.include_router(settings.router)
app.include_router(audit.router)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    """Redirect unknown paths to dashboard (or login if unauthenticated)."""
    if request.session.get("user"):
        return RedirectResponse("/dashboard", status_code=302)
    return RedirectResponse("/login", status_code=302)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info",
    )
