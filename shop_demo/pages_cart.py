"""Giỏ hàng, mã giảm giá và thanh toán (ưu đãi sự kiện áp dụng như đã công bố cho trợ lý)."""
import secrets
import sqlite3
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from db import get_db
from offers import coupon_discount
from web import (PAYMENT_LABEL, back, cart_lines, cart_totals, current_user, flash, render, shipping_fee,
                 to_login)

router = APIRouter()


@router.post("/cart/add")
def cart_add(request: Request, product_id: int = Form(...), qty: int = Form(1), buy_now: str = Form(""),
             db: sqlite3.Connection = Depends(get_db)):
    p = db.execute("SELECT id, stock, name FROM products WHERE id = ?", (product_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Không tìm thấy sản phẩm")
    cart = request.session.get("cart", {})
    new_qty = min(cart.get(str(p["id"]), 0) + max(qty, 1), 10)
    if new_qty > p["stock"]:
        flash(request, f"“{p['name']}” chỉ còn {p['stock']} sản phẩm.", "warn")
        return RedirectResponse(f"/products/{p['id']}", status_code=303)
    cart[str(p["id"])] = new_qty
    request.session["cart"] = cart
    flash(request, f"Đã thêm “{p['name']}” vào giỏ hàng.")
    return RedirectResponse("/checkout" if buy_now else "/cart", status_code=303)


@router.get("/cart")
def cart_view(request: Request, db: sqlite3.Connection = Depends(get_db)):
    return render(request, db, "cart.html", **cart_totals(request, db))


@router.post("/cart/remove")
def cart_remove(request: Request, product_id: int = Form(...)):
    cart = request.session.get("cart", {})
    cart.pop(str(product_id), None)
    request.session["cart"] = cart
    flash(request, "Đã xoá sản phẩm khỏi giỏ hàng.")
    return RedirectResponse("/cart", status_code=303)


@router.post("/cart/clear")
def cart_clear(request: Request):
    request.session["cart"] = {}
    request.session.pop("coupon", None)
    return RedirectResponse("/cart", status_code=303)


@router.post("/cart/coupon")
def cart_coupon(request: Request, code: str = Form(""), next: str = Form("/cart"),
                db: sqlite3.Connection = Depends(get_db)):
    code = code.strip().upper()
    if not code:
        request.session.pop("coupon", None)
        flash(request, "Đã bỏ mã giảm giá.")
        return back(next)
    lines, subtotal = cart_lines(request, db)
    disc, err = coupon_discount(code, subtotal, shipping_fee(subtotal))
    if not lines:
        err = "Giỏ hàng đang trống."
    if err:
        flash(request, err, "warn")
    else:
        request.session["coupon"] = code
        flash(request, f"Áp dụng mã {code}: giảm {disc:,}₫.".replace(",", "."))
    return back(next)


@router.post("/cart/update")
def cart_update(request: Request, product_id: int = Form(...), qty: int = Form(...),
                db: sqlite3.Connection = Depends(get_db)):
    cart = request.session.get("cart", {})
    if str(product_id) in cart:
        stock = db.execute("SELECT stock FROM products WHERE id = ?", (product_id,)).fetchone()
        if qty <= 0 or not stock or stock["stock"] <= 0:  # hết hàng: bỏ dòng, không để số lượng 0
            cart.pop(str(product_id))
        else:
            cart[str(product_id)] = min(qty, 10, stock["stock"])
        request.session["cart"] = cart
    return RedirectResponse("/cart", status_code=303)


@router.get("/checkout")
def checkout_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    t = cart_totals(request, db)
    if not t["lines"]:
        flash(request, "Giỏ hàng đang trống.", "warn")
        return RedirectResponse("/", status_code=303)
    return render(request, db, "checkout.html", payments=PAYMENT_LABEL,
                  checkout_key=secrets.token_urlsafe(16), **t)


@router.post("/checkout")
def checkout(request: Request, ship_name: str = Form(...), ship_phone: str = Form(...),
             ship_address: str = Form(...), payment_method: str = Form("cod"),
             checkout_key: str = Form(""), db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    t = cart_totals(request, db, payment_method)  # event rules may depend on the payment method
    lines, fee, discount = t["lines"], t["fee"], t["discount"] + t["event_discount"]
    checkout_key = checkout_key[:64] or None
    if checkout_key:  # cùng một lần bấm "Xác nhận" (bấm hai lần, gửi lại form): về đơn đã tạo
        done = db.execute("SELECT id FROM orders WHERE checkout_key = ? AND customer_id = ?",
                          (checkout_key, user["id"])).fetchone()
        if done:
            return RedirectResponse(f"/orders/{done['id']}", status_code=303)
    if not lines:
        return RedirectResponse("/cart", status_code=303)
    if any(ln["qty"] <= 0 for ln in lines):
        flash(request, "Giỏ hàng có sản phẩm số lượng 0, vui lòng kiểm tra lại.", "warn")
        return RedirectResponse("/cart", status_code=303)
    if payment_method not in PAYMENT_LABEL:
        raise HTTPException(400, "Phương thức thanh toán không hợp lệ")

    # Trừ tồn kho có điều kiện: thất bại thì rollback cả đơn
    for ln in lines:
        cur = db.execute("UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                         (ln["qty"], ln["product"]["id"], ln["qty"]))
        if cur.rowcount == 0:
            db.rollback()
            flash(request, f"“{ln['product']['name']}” không đủ hàng, vui lòng điều chỉnh giỏ.", "warn")
            return RedirectResponse("/cart", status_code=303)

    n = db.execute("SELECT COALESCE(MAX(CAST(SUBSTR(id, 3) AS INTEGER)), 10000) FROM orders").fetchone()[0] + 1
    oid = f"SO{n}"
    now = datetime.now()
    try:
        db.execute(
            "INSERT INTO orders(id,customer_id,status,total,shipping_fee,payment_method,ship_name,ship_phone,"
            "ship_address,placed_at,eta,tracking_code,discount,coupon_code,checkout_key) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)",
            (oid, user["id"], "pending", t["total"], fee, payment_method, ship_name.strip(), ship_phone.strip(),
             ship_address.strip(), now.strftime("%Y-%m-%d %H:%M"), (now + timedelta(days=4)).strftime("%Y-%m-%d"),
             discount, t["coupon"], checkout_key))
    except sqlite3.IntegrityError:  # request song song với cùng checkout_key: huỷ phần trừ kho của lần này
        db.rollback()
        done = db.execute("SELECT id FROM orders WHERE checkout_key = ?", (checkout_key,)).fetchone()
        return RedirectResponse(f"/orders/{done['id']}" if done else "/orders", status_code=303)
    db.executemany("INSERT INTO order_items(order_id,product_id,qty,unit_price) VALUES (?,?,?,?)",
                   [(oid, ln["product"]["id"], ln["qty"], ln["product"]["price"]) for ln in lines])
    if not user["address"]:
        db.execute("UPDATE customers SET address = ?, phone = COALESCE(NULLIF(phone, ''), ?) WHERE id = ?",
                   (ship_address.strip(), ship_phone.strip(), user["id"]))
    request.session["cart"] = {}
    request.session.pop("coupon", None)
    gifts = f" Quà tặng: {'; '.join(t['gifts'])}." if t["gifts"] else ""
    flash(request, f"Đặt hàng thành công! Mã đơn của bạn là {oid}.{gifts}")
    return RedirectResponse(f"/orders/{oid}", status_code=303)
