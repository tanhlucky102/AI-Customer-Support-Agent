"""Mật khẩu (bcrypt), so khoá API, và ký JWT HS256 cho chat (hợp đồng C3).

sign_chat_token ở đây là bản tạm cho đến khi có `sdk/sign_user_token` của M1;
cùng định dạng claims nên thay thế trực tiếp được.
"""
import base64
import hashlib
import hmac
import json
import time
import uuid

import bcrypt

from config import CHAT_TOKEN_TTL, JWT_AUDIENCE, SHOP_ID, TOKEN_SECRET


def hash_password(password: str, rounds: int = 12) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def keys_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode(), expected.encode())


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_chat_token(customer_id: str) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": SHOP_ID,
        "sub": customer_id,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + CHAT_TOKEN_TTL,
        "jti": uuid.uuid4().hex,
    }
    signing_input = (
        _b64(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64(json.dumps(claims, separators=(",", ":")).encode())
    )
    sig = hmac.new(TOKEN_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64(sig)
