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

AGENT_SERVER_URL = os.getenv("AGENT_SERVER_URL", "http://localhost:8000").rstrip("/")
# Server-to-server calls (logout, offers sync); differs from the browser's URL inside Docker.
AGENT_SERVER_INTERNAL_URL = (os.getenv("AGENT_SERVER_INTERNAL_URL") or AGENT_SERVER_URL).rstrip("/")
# The shop's write key for the Agent Server: content pushes and logout (cutting off chat tokens).
WRITE_KEY = os.getenv("DEMO_FAQ_WRITE_KEY", "")
SHOP_URL = os.getenv("DEMO_SHOP_URL", "http://localhost:8001").rstrip("/")
SHOW_WIDGET = os.getenv("SHOW_WIDGET", "1") != "0"
# external = widget của Agent Server (agent AI); demo = chatbot quy tắc cục bộ cũ, dùng khi không chạy Agent Server.
WIDGET_MODE = os.getenv("WIDGET_MODE", "external").strip().lower()
CHAT_TOKEN_TTL = int(os.getenv("CHAT_TOKEN_TTL_SECONDS", "900"))

# Khoá bí mật: nếu .env chưa điền thì sinh khoá ngẫu nhiên cho riêng tiến trình này (không ai biết),
# nên Agent Server không gọi được API và không xác minh được token chat cho tới khi điền .env.
API_KEY = os.getenv("DEMO_SHOP_API_KEY") or secrets.token_urlsafe(32)
TOKEN_SECRET = os.getenv("DEMO_SHOP_TOKEN_SECRET") or secrets.token_urlsafe(32)
SESSION_SECRET = os.getenv("DEMO_SHOP_SESSION_SECRET") or secrets.token_urlsafe(32)

MISSING_KEYS = [name for name in ("DEMO_SHOP_API_KEY", "DEMO_SHOP_TOKEN_SECRET") if not os.getenv(name)]
