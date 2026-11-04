"""Đăng ký, đăng nhập/đăng xuất (kèm thu hồi token chat), tài khoản và token cho widget chat."""
import time
import sqlite3
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from db import get_db
import config
from security import hash_password, sign_chat_token, verify_password
from web import current_user, flash, render, safe_next, to_login

router = APIRouter()


@router.get("/login")
def login_page(request: Request, next: str = "/", db: sqlite3.Connection = Depends(get_db)):
    return render(request, db, "login.html", next=safe_next(next), error=None)


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/"),
          db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM customers WHERE email = ?", (email.strip().lower(),)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return render(request, db, "login.html", status_code=401, next=safe_next(next),
                      error="Email hoặc mật khẩu không đúng.")
    cart = request.session.get("cart", {})
    request.session.clear()  # chống session fixation; giữ lại giỏ hàng
    request.session["cid"] = row["id"]
    request.session["cart"] = cart
    flash(request, f"Xin chào {row['full_name']}!")
    return RedirectResponse(safe_next(next), status_code=303)


@router.get("/register")
def register_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    return render(request, db, "register.html", error=None, form={})


@router.post("/register")
def register(request: Request, full_name: str = Form(...), email: str = Form(...), phone: str = Form(""),
             password: str = Form(...), db: sqlite3.Connection = Depends(get_db)):
    email = email.strip().lower()
    form = {"full_name": full_name, "email": email, "phone": phone}
    error = None
    if len(password) < 8:
        error = "Mật khẩu tối thiểu 8 ký tự."
    elif db.execute("SELECT 1 FROM customers WHERE email = ?", (email,)).fetchone():
        error = "Email đã được đăng ký."
    if error:
        return render(request, db, "register.html", status_code=400, error=error, form=form)
    password_hash = hash_password(password)
    for _ in range(5):  # hai người đăng ký cùng lúc có thể lấy cùng số: thử số kế tiếp
        n = db.execute("SELECT COALESCE(MAX(CAST(SUBSTR(id, 2) AS INTEGER)), 0) FROM customers").fetchone()[0] + 1
        cid = f"C{n:04d}"
        try:
            db.execute("INSERT INTO customers(id,email,password_hash,full_name,phone,address,tier,created_at) "
                       "VALUES (?,?,?,?,?,?,?,?)",
                       (cid, email, password_hash, full_name.strip(), phone.strip(), "", "standard",
                        datetime.now().strftime("%Y-%m-%d")))
            break
        except sqlite3.IntegrityError:
            if db.execute("SELECT 1 FROM customers WHERE email = ?", (email,)).fetchone():
                return render(request, db, "register.html", status_code=400,
                              error="Email đã được đăng ký.", form=form)
    else:
        raise HTTPException(503, "Vui lòng thử lại.")
    request.session["cid"] = cid
    flash(request, "Tạo tài khoản thành công!")
    return RedirectResponse("/", status_code=303)


def revoke_chat_tokens(customer_id: str) -> None:
    """Logging out also cuts off the chat tokens this customer received (Agent Server, contract C3)."""
    if not config.WRITE_KEY:
        return
    try:
        httpx.post(f"{config.AGENT_SERVER_INTERNAL_URL}/v1/shops/{config.SHOP_ID}/logout",
                   json={"customer_id": customer_id, "logout_at": int(time.time())},
                   headers={"Authorization": f"Bearer {config.WRITE_KEY}"}, timeout=3)
    except httpx.HTTPError:
        pass  # Logging out must not fail when the chat server is down; tokens still expire in 15 min.


@router.post("/logout")
def logout(request: Request):
    customer_id = request.session.get("cid")
    request.session.clear()
    if customer_id:
        revoke_chat_tokens(customer_id)
    return RedirectResponse("/", status_code=303)


@router.get("/account")
def account_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    stats = db.execute("SELECT COUNT(*) AS n, COALESCE(SUM(total), 0) AS spent FROM orders "
                       "WHERE customer_id = ? AND status NOT IN ('cancelled', 'returned')", (user["id"],)).fetchone()
    return render(request, db, "account.html", stats=stats)


@router.post("/account")
def account_update(request: Request, full_name: str = Form(...), phone: str = Form(""), address: str = Form(""),
                   db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    db.execute("UPDATE customers SET full_name = ?, phone = ?, address = ? WHERE id = ?",
               (full_name.strip() or user["full_name"], phone.strip(), address.strip(), user["id"]))
    flash(request, "Đã cập nhật thông tin tài khoản.")
    return RedirectResponse("/account", status_code=303)


@router.post("/account/password")
def account_password(request: Request, old_password: str = Form(...), new_password: str = Form(...),
                     db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    if not verify_password(old_password, user["password_hash"]):
        flash(request, "Mật khẩu hiện tại không đúng.", "warn")
    elif len(new_password) < 8:
        flash(request, "Mật khẩu mới tối thiểu 8 ký tự.", "warn")
    else:
        db.execute("UPDATE customers SET password_hash = ? WHERE id = ?", (hash_password(new_password), user["id"]))
        flash(request, "Đã đổi mật khẩu.")
    return RedirectResponse("/account", status_code=303)


# ---------- token cho chat widget ----------
@router.get("/api/chat-token")
def chat_token(request: Request, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return JSONResponse({"error": "not_logged_in"}, status_code=401, headers={"Cache-Control": "no-store"})
    return JSONResponse({"token": sign_chat_token(user["id"]), "expires_in": config.CHAT_TOKEN_TTL,
                         "customer_name": user["full_name"]}, headers={"Cache-Control": "no-store"})
