"""Mật khẩu (bcrypt), so khoá API, và ký JWT HS256 cho chat (hợp đồng C3) bằng SDK của framework."""
import hmac
import sys
from pathlib import Path

import bcrypt

from config import CHAT_TOKEN_TTL, SHOP_ID, TOKEN_SECRET

# Trong monorepo này SDK nằm ở thư mục gốc; website thật cài SDK như một gói thay vì sửa sys.path.
sys.path.append(str(Path(__file__).resolve().parent.parent / "sdk"))
from ati_sdk import sign_user_token  # noqa: E402


def hash_password(password: str, rounds: int = 12) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def keys_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode(), expected.encode())


def sign_chat_token(customer_id: str) -> str:
    return sign_user_token(customer_id, shop_id=SHOP_ID, secret=TOKEN_SECRET, ttl_seconds=CHAT_TOKEN_TTL)
