import os
from pathlib import Path


class Config:
    SECRET_KEY = os.getenv("SAULINFO_SECRET_KEY", "change-me")
    HOST = os.getenv("SAULINFO_HOST", "0.0.0.0")
    PORT = int(os.getenv("SAULINFO_PORT", "8080"))
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://www.saulinfo.ru").rstrip("/")
    ALLOW_SELF_REGISTRATION = (os.getenv("ALLOW_SELF_REGISTRATION", "true").strip().lower() in {"1", "true", "yes", "on"})
    DATA_DIR = Path(os.getenv("SAULINFO_DATA_DIR", "/data"))
    AUTH_DB_PATH = os.getenv("SAULINFO_AUTH_DB_PATH", str(DATA_DIR / "saulinfo_auth.db"))
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    GOOGLE_OAUTH_SCOPE = os.getenv("GOOGLE_OAUTH_SCOPE", "openid email profile").strip()
    SITE_ACCOUNT_RETENTION_DAYS = int(os.getenv("SITE_ACCOUNT_RETENTION_DAYS", "90"))
    SITE_CLEANUP_INTERVAL_HOURS = int(os.getenv("SITE_CLEANUP_INTERVAL_HOURS", "12"))

    SHOP_UPDATE_DB_PATH = os.getenv("SHOP_UPDATE_DB_PATH", "/integrations/shop-update/users.db")
    SHOP_UPDATE_PANEL_URL = os.getenv("SHOP_UPDATE_PANEL_URL", "https://panel.saulinfo.ru/control-room-saul").rstrip("/")
