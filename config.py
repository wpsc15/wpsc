import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
SECRET_KEY = os.environ.get("WEBPANEL_SECRET", "webpanel-super-secret-key-change-me")
DB_PATH = BASE_DIR / "webpanel.db"
DATABASE_BACKUP_DIR = BASE_DIR / "database_backups"
PANEL_VERSION = "1.0.0"
PANEL_NAME = "WebPanel"
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASS = "admin123"  # change after first login
