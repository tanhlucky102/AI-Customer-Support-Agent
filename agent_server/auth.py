"""Verify the chat token (contract C3) that the shop backend signs for a logged-in customer."""

import base64
import hashlib
import hmac
import json
import time

JWT_AUDIENCE = "ati-agent"
# The SDK issues 15-minute tokens. A longer-lived token is refused, which also bounds how long a
# logout cut-off must be kept (store.revoke_customer_tokens).
MAX_TOKEN_LIFETIME = 3600


def verify_customer_token(token: str, shop: dict) -> str | None:
    """Return the signed customer ID, or None for an invalid/expired token."""
    claims = verify_customer_claims(token, shop)
    return claims["sub"] if claims else None


def verify_customer_claims(token: str, shop: dict, expired_grace: int = 0) -> dict | None:
    """Return the claims (sub, iat, exp, ...) of a valid token signed for this shop, or None.
    Whether the customer has logged out since is checked by the caller (store.token_revoked).
    `expired_grace` accepts a recently expired token; logout uses it, since logging out only
    removes rights."""
    try:
        if len(token) > 4096:
            return None
        encoded_header, encoded_claims, encoded_signature = token.split(".")

        def decode(part: str) -> bytes:
            return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

        header = json.loads(decode(encoded_header))
        claims = json.loads(decode(encoded_claims))
        if not isinstance(header, dict) or not isinstance(claims, dict):
            return None
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
        if not isinstance(claims.get("sub"), str) or not claims["sub"] or claims["sub"].startswith("guest:"):
            return None  # "guest:" IDs belong to visitors without an account (app.require_customer)
        if not isinstance(claims.get("exp"), int) or claims["exp"] + expired_grace <= now:
            return None
        if not isinstance(claims.get("iat"), int) or claims["iat"] > now + 60:
            return None
        if claims["exp"] - claims["iat"] > MAX_TOKEN_LIFETIME:
            return None
        return claims
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, RecursionError):
        return None  # Any malformed token is a 401, never a server error.
