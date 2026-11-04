"""Đơn hàng của tôi: danh sách, chi tiết, huỷ, mua lại và nút [Demo] chuyển trạng thái."""
import random
import string
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from db import get_db
from web import NEXT_STATUS, STATUS_LABEL, current_user, flash, order_items, own_order, render, to_login

router = APIRouter()


@router.get("/orders")
def my_orders(request: Request, status: str = "", db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    sql, args = "SELECT * FROM orders WHERE customer_id = ?", [user["id"]]
    if status in STATUS_LABEL:
        sql += " AND status = ?"
        args.append(status)
    orders = db.execute(sql + " ORDER BY placed_at DESC", args).fetchall()
    return render(request, db, "orders.html", orders=orders, status=status)


@router.get("/orders/{order_id}")
def order_detail(request: Request, order_id: str, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    order = own_order(request, db, order_id, user)
    return render(request, db, "order_detail.html", order=order, items=order_items(db, order_id))


@router.post("/orders/{order_id}/cancel")
def cancel_order(request: Request, order_id: str, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    order = own_order(request, db, order_id, user)
    if order["status"] not in ("pending", "confirmed"):
        flash(request, "Chỉ huỷ được đơn đang chờ xác nhận hoặc đã xác nhận.", "warn")
    else:
        # Có điều kiện: hai lần huỷ cùng lúc chỉ một lần đổi trạng thái, nên kho chỉ được hoàn một lần.
        cancelled = db.execute("UPDATE orders SET status = 'cancelled' WHERE id = ? AND customer_id = ? "
                               "AND status IN ('pending', 'confirmed')", (order_id, user["id"])).rowcount
        if cancelled:
            for it in order_items(db, order_id):  # hoàn tồn kho
                db.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (it["qty"], it["product_id"]))
        flash(request, f"Đã huỷ đơn {order_id}.")
    return RedirectResponse(f"/orders/{order_id}", status_code=303)


@router.post("/orders/{order_id}/advance")
def advance_order(request: Request, order_id: str, db: sqlite3.Connection = Depends(get_db)):
    """CÔNG CỤ DEMO: mô phỏng kho/vận chuyển đẩy đơn sang trạng thái kế tiếp để thử chatbot."""
    user = current_user(request, db)
    if not user:
        return to_login(request)
    order = own_order(request, db, order_id, user)
    nxt = NEXT_STATUS.get(order["status"])
    if not nxt:
        flash(request, "Đơn này không thể chuyển tiếp.", "warn")
    else:
        tracking = order["tracking_code"]
        if nxt == "shipping" and not tracking:
            tracking = random.choice(["GHN", "GHTK", "VNP", "JT"]) + "".join(random.choices(string.digits, k=10))
        delivered = datetime.now().strftime("%Y-%m-%d") if nxt == "delivered" else order["delivered_at"]
        db.execute("UPDATE orders SET status = ?, tracking_code = ?, delivered_at = ? WHERE id = ?",
                   (nxt, tracking, delivered, order_id))
        flash(request, f"[Demo] Đơn {order_id} chuyển sang: {STATUS_LABEL[nxt]}.")
    return RedirectResponse(f"/orders/{order_id}", status_code=303)


@router.post("/orders/{order_id}/reorder")
def reorder(request: Request, order_id: str, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    own_order(request, db, order_id, user)
    cart, skipped = request.session.get("cart", {}), 0
    for it in order_items(db, order_id):
        p = db.execute("SELECT stock FROM products WHERE id = ?", (it["product_id"],)).fetchone()
        if not p or p["stock"] <= 0:
            skipped += 1
            continue
        key = str(it["product_id"])
        cart[key] = min(cart.get(key, 0) + it["qty"], 10, p["stock"])
    request.session["cart"] = cart
    flash(request, f"Đã thêm sản phẩm của đơn {order_id} vào giỏ hàng."
          + (f" ({skipped} sản phẩm đã hết hàng)" if skipped else ""), "warn" if skipped else "ok")
    return RedirectResponse("/cart", status_code=303)
