"""Rule-based answers used when the language model is unavailable (no key, timeout, HTTP error).

No text is generated: replies quote the shop's order data or one retrieved document section.
"""

import asyncio
import re

from shared.text import fold, source_label

# Order codes such as SO10293, PKX204518 or SO#10293: a letter prefix attached to 5-8 digits.
# No space is allowed, so "sdt 0912345678" or "ck 500000" are not taken for order codes.
ORDER_ID = re.compile(r"\b([A-Z]{2,4})[#-]?(\d{5,8})\b", re.IGNORECASE)
MY_ORDERS = ("don cua toi", "don hang cua toi", "lich su don", "tra cuu don", "kiem tra don",
             "don gan day", "my order")
OFFLINE_NOTE = "(Trợ lý AI đang tạm gián đoạn, đây là câu trả lời tự động.)"


def vnd(amount) -> str:
    try:
        return f"{int(float(amount)):,}".replace(",", ".") + "₫"
    except (TypeError, ValueError):
        return str(amount)  # Some shop APIs send money as text such as "590000.00 VND".


def order_line(order: dict) -> str:
    line = f"Đơn {order['id']}: {order['status_label']}"
    if order.get("eta"):
        line += f", dự kiến giao {order['eta']}"
    if order.get("tracking_code"):
        line += f", mã vận đơn {order['tracking_code']}"
    if order.get("total") is not None:
        line += f", tổng tiền {vnd(order['total'])}"
    return line + "."


async def answer(ctx, message: str) -> tuple[str, str, list[dict]]:
    """Return (status, reply, sources)."""
    text = fold(message)
    capabilities = ctx.adapter.capabilities if ctx.adapter else frozenset()
    match = ORDER_ID.search(message)
    try:
        if match and "get_order" in capabilities:
            order = await ctx.adapter.get_order(match.group(1).upper() + match.group(2), ctx.customer_id)
            if order is None:
                return "Đã kiểm tra đơn hàng", "Không tìm thấy đơn này trong tài khoản của bạn.", []
            links = [{"label": f"Xem đơn {order['id']}", "href": order["url"]}] if order.get("url") else []
            return "Đã kiểm tra đơn hàng", f"{order_line(order)} {OFFLINE_NOTE}", links
        if any(phrase in text for phrase in MY_ORDERS) and "list_orders" in capabilities:
            orders = await ctx.adapter.list_orders(ctx.customer_id, 3, None)
            if not orders:
                return "Đã kiểm tra đơn hàng", "Tài khoản của bạn chưa có đơn hàng.", []
            reply = "Các đơn gần đây của bạn:\n" + "\n".join(f"- {order_line(o)}" for o in orders)
            return "Đã kiểm tra đơn hàng", f"{reply}\n{OFFLINE_NOTE}", []
    except Exception:  # CommerceError or malformed shop data: the fallback itself must not fail.
        return "Lỗi kết nối shop", "Chưa kết nối được hệ thống đơn hàng của shop. Vui lòng thử lại sau.", []
    try:
        hits = await asyncio.to_thread(ctx.search, message, ctx.shop["shop_id"], None)
    except Exception:  # The fallback must never fail; without search it gives the contact reply.
        hits = []
    if hits:
        best = hits[0]
        body = best["content"].split("\n", 1)[-1].strip()
        if len(body) > 700:
            body = body[:700].rsplit(" ", 1)[0] + "..."
        reply = f"Theo tài liệu “{source_label(best)}”:\n{body}\n{OFFLINE_NOTE}"
        return "Đã tìm trong tài liệu của shop", reply, [
            {"ref": 1, "label": source_label(best), "href": best.get("url")}]
    return ("Chưa có dữ liệu phù hợp",
            "Mình chưa tìm thấy thông tin phù hợp trong tài liệu của shop. Vui lòng liên hệ shop "
            "qua thông tin trên website để được hỗ trợ chính xác.",
            [{"label": "Thông tin liên hệ của shop", "href": ctx.shop["shop_url"]}])
