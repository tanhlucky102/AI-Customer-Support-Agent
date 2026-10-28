"""Chatbot tạm thời cho shop demo; trả lời từ dữ liệu shop, không gọi LLM."""

import re
import sqlite3
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from db import get_db


ORDER_ID = re.compile(r"\bSO[\s#-]*(\d{5,})\b", re.IGNORECASE)


class ChatMessage(BaseModel):
    message: str = Field(min_length=1, max_length=500)


def normalize(text: str) -> str:
    text = text.lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def policy_answer(text: str, policies: dict) -> tuple[str, list[dict]] | None:
    """Chỉ dùng các điều khoản hiển thị trên chính website demo."""
    if "tra gop" in text:
        slug, wanted = "tra-gop", None
    elif any(term in text for term in ("bao hanh", "loi nha san xuat")):
        slug, wanted = "bao-hanh", None
    elif "huy don" in text:
        slug, wanted = "doi-tra", {"Huỷ đơn"}
    elif "hoan tien" in text:
        slug, wanted = "doi-tra", {"Điều kiện", "Hoàn tiền"}
    elif any(term in text for term in ("doi tra", "tra hang", "hoan tra")):
        slug, wanted = "doi-tra", {"Thời hạn", "Điều kiện", "Hoàn tiền"}
    elif any(term in text for term in ("van chuyen", "giao hang", "phi ship", "freeship", "ma van don")):
        slug, wanted = "van-chuyen", None
    elif any(term in text for term in ("thanh toan", "chuyen khoan", "cod")):
        slug, wanted = "thanh-toan", None
    else:
        return None

    title, sections = policies[slug]
    lines = [f"{heading}: {body}" for heading, body in sections if wanted is None or heading in wanted]
    return "\n".join(lines), [{"label": title, "href": f"/policy/{slug}"}]


def format_vnd(value: int) -> str:
    return f"{value:,}".replace(",", ".") + " ₫"


def create_demo_chat_router(policies: dict, status_labels: dict) -> APIRouter:
    router = APIRouter()

    @router.post("/api/demo-chat")
    def demo_chat(payload: ChatMessage, request: Request, db: sqlite3.Connection = Depends(get_db)):
        message = payload.message.strip()
        if not message:
            raise HTTPException(400, "empty_message")

        text = normalize(message)
        customer_id = request.session.get("cid")
        customer = (db.execute("SELECT id FROM customers WHERE id = ?", (customer_id,)).fetchone()
                    if customer_id else None)
        customer_id = customer["id"] if customer else None
        if not customer_id:
            raise HTTPException(401, "login_required")
        policy = policy_answer(text, policies)
        order_match = ORDER_ID.search(message)
        sources: list[dict] = []

        if order_match:
            order_id = "SO" + order_match.group(1)
            if not customer_id:
                reply = "Bạn cần đăng nhập để tra cứu đơn hàng. Sau khi đăng nhập, hãy hỏi lại mã đơn này."
                sources = [{"label": "Đăng nhập", "href": "/login"}]
            else:
                order = db.execute(
                    "SELECT id, status, total, eta, tracking_code FROM orders WHERE id = ? AND customer_id = ?",
                    (order_id, customer_id),
                ).fetchone()
                if not order:
                    reply = "Không tìm thấy đơn hàng này trong tài khoản của bạn. Vui lòng kiểm tra lại mã đơn."
                else:
                    reply = (f"Đơn {order['id']} hiện {status_labels.get(order['status'], order['status'])}. "
                             f"Dự kiến giao: {order['eta'] or 'chưa có'}. Tổng tiền: {format_vnd(order['total'])}.")
                    if order["tracking_code"]:
                        reply += f" Mã vận đơn: {order['tracking_code']}."
                    sources = [{"label": f"Xem đơn {order_id}", "href": f"/orders/{order_id}"}]
                    if policy:
                        reply += "\n\nTheo chính sách shop: " + policy[0]
                        sources.extend(policy[1])
        elif policy:
            reply, sources = policy
        elif any(term in text for term in ("don cua toi", "don hang cua toi", "lich su don", "tra cuu don", "kiem tra don")):
            if not customer_id:
                reply = "Bạn cần đăng nhập để xem đơn hàng của mình."
                sources = [{"label": "Đăng nhập", "href": "/login"}]
            else:
                orders = db.execute(
                    "SELECT id, status FROM orders WHERE customer_id = ? ORDER BY placed_at DESC LIMIT 3",
                    (customer_id,),
                ).fetchall()
                if orders:
                    reply = "Các đơn gần đây:\n" + "\n".join(
                        f"{row['id']}: {status_labels.get(row['status'], row['status'])}" for row in orders
                    ) + "\nBạn có thể gửi mã đơn để xem chi tiết."
                else:
                    reply = "Tài khoản của bạn chưa có đơn hàng."
                sources = [{"label": "Đơn hàng của tôi", "href": "/orders"}]
        elif text.strip() in ("chao", "xin chao", "hi", "hello", "help", "tro giup"):
            reply = "Xin chào! Mình có thể trả lời về đổi trả, bảo hành, giao hàng, thanh toán và tra đơn hàng của bạn."
        else:
            reply = ("Mình chưa xác định được câu trả lời từ dữ liệu shop hiện có. "
                     "Vui lòng liên hệ trực tiếp với shop để được hỗ trợ chính xác.")

        return JSONResponse({"reply": reply, "sources": sources}, headers={"Cache-Control": "no-store"})

    return router
