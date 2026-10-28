"""Deterministic test engine for the embeddable widget. No LLM or RAG."""

import base64
import hashlib
import hmac
import json
import os
import re
import time
import unicodedata
from urllib.parse import quote

import httpx

from knowledge_store import DEMO_DATA, list_faqs


SHOP_API_URL = os.getenv("DEMO_SHOP_API_URL", "http://localhost:8001").rstrip("/")
SHOP_API_KEY = os.getenv("DEMO_SHOP_API_KEY") or "dev-only-change-me-api-key"
TOKEN_SECRET = os.getenv("DEMO_SHOP_TOKEN_SECRET") or "dev-only-change-me-token-secret"
JWT_AUDIENCE = "ati-agent"
ORDER_ID = re.compile(r"\bSO[\s#-]*(\d{5,})\b", re.IGNORECASE)
STATUS_LABEL = {
    "pending": "Chờ xác nhận", "confirmed": "Đã xác nhận", "shipping": "Đang giao",
    "delivered": "Đã giao", "cancelled": "Đã huỷ", "returned": "Đã hoàn trả",
}


def normalize(value: str) -> str:
    value = value.lower().replace("đ", "d")
    return "".join(char for char in unicodedata.normalize("NFD", value)
                   if unicodedata.category(char) != "Mn")


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
        expected = hmac.new(secret.encode(),
                            f"{encoded_header}.{encoded_claims}".encode(),
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


def source(faq: dict) -> list[dict]:
    return ([{"label": faq["question"], "href": faq["source_url"]}]
            if faq.get("source_url") else [])


def matching_faq(message: str, shop_id: str) -> dict | None:
    text = normalize(message)
    query_words = {word for word in re.findall(r"[a-z0-9]+", text) if len(word) >= 3}
    best, best_score = None, 0
    for faq in list_faqs(shop_id):
        question = normalize(faq["question"])
        score = 100 if text == question else 0
        if question in text and len(question) >= 8:
            score = max(score, 90)
        for keyword in faq["keywords"]:
            phrase = normalize(keyword)
            if phrase and phrase in text:
                score = max(score, 70 + min(len(phrase), 20))
        question_words = {word for word in re.findall(r"[a-z0-9]+", question) if len(word) >= 3}
        overlap = len(query_words & question_words)
        if overlap >= 2 and len(question_words) >= 2:
            score = max(score, int(50 * overlap / len(question_words)))
        if score > best_score:
            best, best_score = faq, score
    return best if best_score >= 45 else None


def vnd(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + " ₫"


async def shop_get(path: str) -> httpx.Response:
    async with httpx.AsyncClient(base_url=SHOP_API_URL, timeout=6.0) as client:
        return await client.get(path, headers={"Authorization": f"Bearer {SHOP_API_KEY}"})


async def answer(message: str, customer_id: str | None, shop: dict) -> tuple[str, str, list[dict]]:
    """FAQ comes from our database; live orders come from the demo shop API."""
    text = normalize(message)
    faq = matching_faq(message, shop["shop_id"])
    match = ORDER_ID.search(message)
    wants_orders = any(phrase in text for phrase in (
        "don cua toi", "don hang cua toi", "lich su don", "tra cuu don", "kiem tra don"
    ))
    faq_sources = source(faq) if faq else []

    if match or wants_orders:
        if shop["shop_id"] != DEMO_DATA["shop_id"]:
            return ("Chưa cấu hình tra đơn", "Website này chưa kết nối API đơn hàng; chatbot hiện chỉ hỗ trợ FAQ.", [])
        if not customer_id:
            return ("Cần đăng nhập", "Bạn cần đăng nhập trên website để tra đơn hàng của mình.",
                    [{"label": "Đăng nhập", "href": f"{shop['shop_url']}/login"}])
        try:
            cid = quote(customer_id, safe="")
            if match:
                order_id = "SO" + match.group(1)
                response = await shop_get(f"/api/v1/customers/{cid}/orders/{order_id}")
                if response.status_code == 404:
                    return "Đã kiểm tra đơn hàng", "Không tìm thấy đơn này trong tài khoản của bạn.", []
                response.raise_for_status()
                order = response.json()
                reply = (f"Đơn {order['id']} hiện {STATUS_LABEL.get(order['status'], order['status'])}. "
                         f"Dự kiến giao: {order.get('eta') or 'chưa có'}. "
                         f"Tổng tiền: {vnd(order['total'])}.")
                if order.get("tracking_code"):
                    reply += f" Mã vận đơn: {order['tracking_code']}."
                sources = [{"label": f"Xem đơn {order_id}",
                            "href": f"{shop['shop_url']}/orders/{order_id}"}]
                if faq:
                    reply += "\n\nTheo chính sách shop: " + faq["answer"]
                    sources += faq_sources
                return "Đã tra cứu đơn hàng", reply, sources
            response = await shop_get(f"/api/v1/customers/{cid}/orders?limit=3")
            response.raise_for_status()
            orders = response.json()
            if orders:
                lines = [f"{row['id']}: {STATUS_LABEL.get(row['status'], row['status'])}"
                         for row in orders]
                reply = "Các đơn gần đây:\n" + "\n".join(lines)
            else:
                reply = "Tài khoản của bạn chưa có đơn hàng."
            return "Đã tra cứu đơn hàng", reply, [
                {"label": "Đơn hàng của tôi", "href": f"{shop['shop_url']}/orders"}
            ]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return ("Lỗi kết nối shop", "Chưa kết nối được API đơn hàng của shop. Vui lòng thử lại sau.", [])

    if faq:
        return "Đã tìm trong FAQ đã đồng bộ", faq["answer"], faq_sources
    if text.strip() in ("chao", "xin chao", "chao shop", "xin chao shop", "hi", "hello", "help", "tro giup"):
        return ("Sẵn sàng", "Chào bạn! Mình có thể giúp kiểm tra sản phẩm, ưu đãi, "
                "sự kiện shop đã công bố, chính sách và đơn hàng của bạn. "
                "Bạn muốn mình hỗ trợ nội dung nào?", [])
    if text.strip() in ("cam on", "cam on nhe", "thank you", "thanks"):
        return ("Sẵn sàng", "Rất vui được hỗ trợ bạn. Nếu cần, bạn cứ hỏi thêm về sản phẩm, "
                "chính sách hoặc đơn hàng của mình.", [])
    return ("Chưa có dữ liệu phù hợp", "Mình chưa xác định được câu trả lời từ FAQ hoặc dữ liệu shop hiện có. "
            "Vui lòng liên hệ trực tiếp với shop qua thông tin trên website để được hỗ trợ chính xác.",
            [{"label": "Thông tin liên hệ của shop", "href": shop["shop_url"]}])
