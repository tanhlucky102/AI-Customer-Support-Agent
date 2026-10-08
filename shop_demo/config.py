"""Cấu hình shop demo — đọc từ .env ở thư mục gốc dự án."""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")

DB_PATH = Path(os.getenv("DEMO_SHOP_DB", BASE_DIR / "shop.db"))

SHOP_ID = "demo_shop"
PUBLIC_KEY = "pk_demo_shop"
JWT_AUDIENCE = "ati-agent"

AGENT_SERVER_URL = os.getenv("AGENT_SERVER_URL", "http://localhost:8000").rstrip("/")
SHOW_WIDGET = os.getenv("SHOW_WIDGET", "1") != "0"
CHAT_TOKEN_TTL = int(os.getenv("CHAT_TOKEN_TTL_SECONDS", "900"))

# Khoá bí mật: nếu .env chưa điền thì dùng giá trị dev cố định (KHÔNG dùng khi triển khai thật)
_DEV = "dev-only-change-me"
API_KEY = os.getenv("DEMO_SHOP_API_KEY") or _DEV + "-api-key"
TOKEN_SECRET = os.getenv("DEMO_SHOP_TOKEN_SECRET") or _DEV + "-token-secret"
SESSION_SECRET = os.getenv("DEMO_SHOP_SESSION_SECRET") or secrets.token_urlsafe(32)

USING_DEV_KEYS = not (
    os.getenv("DEMO_SHOP_API_KEY") and os.getenv("DEMO_SHOP_TOKEN_SECRET")
)
