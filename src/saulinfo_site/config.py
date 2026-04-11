import os


class Config:
    SECRET_KEY = os.getenv("SAULINFO_SECRET_KEY", "change-me")
    HOST = os.getenv("SAULINFO_HOST", "0.0.0.0")
    PORT = int(os.getenv("SAULINFO_PORT", "8080"))
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://www.saulinfo.ru").rstrip("/")

    VK_CLIENT_ID = os.getenv("VK_CLIENT_ID", "").strip()
    VK_CLIENT_SECRET = os.getenv("VK_CLIENT_SECRET", "").strip()
    VK_REDIRECT_URI = os.getenv("VK_REDIRECT_URI", "").strip()

    SHOP_UPDATE_DB_PATH = os.getenv("SHOP_UPDATE_DB_PATH", "/integrations/shop-update/users.db")
    SHOP_UPDATE_PANEL_URL = os.getenv("SHOP_UPDATE_PANEL_URL", "https://panel.saulinfo.ru").rstrip("/")
