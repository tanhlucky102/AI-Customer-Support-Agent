"""Shop-side SDK: sign the short-lived chat token that the ATI widget sends to the Agent Server.

The shop backend calls this only for a customer who is logged in, e.g. in GET /api/chat-token:

    from ati_sdk import sign_user_token
    token = sign_user_token(user.id, shop_id="demo_shop", secret=CHAT_TOKEN_SECRET)

`secret` is the chat_token_secret printed by provision_shop.py; it must never reach the browser.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid

AUDIENCE = "ati-agent"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_user_token(customer_id: str, *, shop_id: str, secret: str, ttl_seconds: int = 900) -> str:
    """Return an HS256 JWT with claims iss, sub, aud, iat, exp and jti."""
    if customer_id in (None, "") or not shop_id or not secret:
        raise ValueError("customer_id, shop_id and secret are required")
    customer_id = str(customer_id)  # Numeric user IDs are fine; the claim is always a string.
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {"iss": shop_id, "sub": customer_id, "aud": AUDIENCE,
              "iat": now, "exp": now + ttl_seconds, "jti": uuid.uuid4().hex}
    signing_input = (_b64(json.dumps(header, separators=(",", ":")).encode()) + "."
                     + _b64(json.dumps(claims, separators=(",", ":")).encode()))
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64(signature)
