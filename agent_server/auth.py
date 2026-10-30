"""Verify the chat token (contract C3) that the shop backend signs for a logged-in customer."""

import base64
import hashlib
import hmac
import json
import time

JWT_AUDIENCE = "ati-agent"


def verify_customer_token(token: str, shop: dict) -> str | None:
    """Return the signed customer ID, or None for an invalid/expired token."""
    try:
        if len(token) > 4096:
            return None
        encoded_header, encoded_claims, encoded_signature = token.split(".")

        def decode(part: str) -> bytes:
            return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

        header = json.loads(decode(encoded_header))
        claims = json.loads(decode(encoded_claims))
        signature = decode(encoded_signature)
        secret = shop.get("chat_token_secret")
        if not secret:
            return None
        expected = hmac.new(secret.encode(), f"{encoded_header}.{encoded_claims}".encode(),
                            hashlib.sha256).digest()
        now = int(time.time())
        if header.get("alg") != "HS256" or not hmac.compare_digest(signature, expected):
            return None
        if claims.get("iss") != shop["shop_id"] or claims.get("aud") != JWT_AUDIENCE:
            return None
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            return None
        if not isinstance(claims.get("exp"), int) or claims["exp"] <= now:
            return None
        if not isinstance(claims.get("iat"), int) or claims["iat"] > now + 60:
            return None
        return claims["sub"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None
