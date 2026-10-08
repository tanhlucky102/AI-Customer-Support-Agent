"""Website shop demo — "khách hàng" của framework chatbot.

Chạy:  python seed.py && uvicorn app:app --port 8001 --reload   (trong thư mục shop_demo/)
"""
import random
import sqlite3
import string
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import config
from api import router as api_router
from db import get_db, init_db
from security import hash_password, sign_chat_token, verify_password

PAGE_SIZE = 12
SHIPPING_FREE_FROM = 500_000
SHIPPING_FEE = 30_000

STATUS_LABEL = {
    "pending": "Chờ xác nhận", "confirmed": "Đã xác nhận", "shipping": "Đang giao",
    "delivered": "Đã giao", "cancelled": "Đã huỷ", "returned": "Đã hoàn trả",
}
NEXT_STATUS = {"pending": "confirmed", "confirmed": "shipping", "shipping": "delivered"}
PAYMENT_LABEL = {"cod": "Thanh toán khi nhận hàng (COD)", "bank": "Chuyển khoản ngân hàng"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if config.USING_DEV_KEYS:
        print("[shop_demo] CẢNH BÁO: DEMO_SHOP_API_KEY / DEMO_SHOP_TOKEN_SECRET chưa có trong .env — "
              "đang dùng khoá dev mặc định.")
    yield


app = FastAPI(title="Demo Mobile", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, same_site="lax",
                   https_only=False, max_age=60 * 60 * 24 * 7)  # cookie HttpOnly theo mặc định
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "static"), name="static")
app.include_router(api_router)

templates = Jinja2Templates(directory=config.BASE_DIR / "templates")
templates.env.filters["vnd"] = lambda v: f"{int(v):,}".replace(",", ".") + " ₫"
templates.env.filters["status_label"] = lambda s: STATUS_LABEL.get(s, s)
templates.env.filters["payment_label"] = lambda s: PAYMENT_LABEL.get(s, s)
CAT_ICON = {"Điện thoại": "📱", "Ốp lưng": "🛡️", "Sạc & cáp": "🔌", "Tai nghe": "🎧", "Pin dự phòng": "🔋",
            "Dán màn hình": "✨"}
templates.env.globals.update(STATUS_LABEL=STATUS_LABEL, NEXT_STATUS=NEXT_STATUS, cfg=config, CAT_ICON=CAT_ICON)


# ---------- helpers ----------
def current_user(request: Request, db: sqlite3.Connection) -> sqlite3.Row | None:
    cid = request.session.get("cid")
    if not cid:
        return None
    return db.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()


def safe_next(url: str | None) -> str:
    return url if url and url.startswith("/") and not url.startswith("//") else "/"


def flash(request: Request, msg: str, kind: str = "ok") -> None:
    request.session["flash"] = {"msg": msg, "kind": kind}


def render(request: Request, db: sqlite3.Connection, name: str, status_code: int = 200, **ctx):
    cart = request.session.get("cart", {})
    user = current_user(request, db)
    favs = ({r[0] for r in db.execute("SELECT product_id FROM wishlist WHERE customer_id = ?", (user["id"],))}
            if user else set())
    ctx.update(user=user, cart_count=sum(cart.values()), favs=favs, flash=request.session.pop("flash", None))
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def to_login(request: Request) -> RedirectResponse:
    flash(request, "Vui lòng đăng nhập để tiếp tục.", "warn")
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


def cart_lines(request: Request, db: sqlite3.Connection) -> tuple[list[dict], int]:
    cart: dict[str, int] = request.session.get("cart", {})
    lines, subtotal = [], 0
    for pid, qty in cart.items():
        p = db.execute("SELECT * FROM products WHERE id = ?", (int(pid),)).fetchone()
        if not p:
            continue
        line = {"product": p, "qty": qty, "amount": qty * p["price"]}
        lines.append(line)
        subtotal += line["amount"]
    return lines, subtotal


def shipping_fee(subtotal: int) -> int:
    return 0 if subtotal == 0 or subtotal >= SHIPPING_FREE_FROM else SHIPPING_FEE


COUPONS = {
    "GIAM50K": "Giảm 50.000₫ cho đơn từ 500.000₫",
    "GIAM5": "Giảm 5% (tối đa 300.000₫)",
    "FREESHIP": "Miễn phí vận chuyển",
    "WELCOME100": "Giảm 100.000₫ cho đơn từ 2.000.000₫",
}


def coupon_discount(code: str, subtotal: int, fee: int) -> tuple[int, str | None]:
    """Trả về (số tiền giảm, lỗi)."""
    if code == "GIAM50K":
        return (50_000, None) if subtotal >= 500_000 else (0, "Mã GIAM50K áp dụng cho đơn từ 500.000₫.")
    if code == "GIAM5":
        return min(subtotal * 5 // 100, 300_000), None
    if code == "FREESHIP":
        return (fee, None) if fee else (0, "Đơn của bạn đã được miễn phí vận chuyển.")
    if code == "WELCOME100":
        return (100_000, None) if subtotal >= 2_000_000 else (0, "Mã WELCOME100 áp dụng cho đơn từ 2.000.000₫.")
    return 0, "Mã giảm giá không hợp lệ."


def cart_totals(request: Request, db: sqlite3.Connection) -> dict:
    lines, subtotal = cart_lines(request, db)
    fee = shipping_fee(subtotal)
    code = request.session.get("coupon")
    discount = 0
    if code and lines:
        discount, err = coupon_discount(code, subtotal, fee)
        if err:  # giỏ thay đổi làm mã không còn hợp lệ
            request.session.pop("coupon", None)
            code, discount = None, 0
    elif code:
        request.session.pop("coupon", None)
        code = None
    return {"lines": lines, "subtotal": subtotal, "fee": fee, "discount": discount, "coupon": code,
            "total": subtotal + fee - discount, "free_from": SHIPPING_FREE_FROM, "coupons": COUPONS}


def back(next_url: str | None, fallback: str = "/") -> RedirectResponse:
    return RedirectResponse(safe_next(next_url) if next_url else fallback, status_code=303)


def order_items(db: sqlite3.Connection, order_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT p.id AS product_id, p.name, i.qty, i.unit_price FROM order_items i "
        "JOIN products p ON p.id = i.product_id WHERE i.order_id = ?", (order_id,)).fetchall()


def own_order(request: Request, db: sqlite3.Connection, order_id: str, user) -> sqlite3.Row:
    row = db.execute("SELECT * FROM orders WHERE id = ? AND customer_id = ?", (order_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Không tìm thấy đơn hàng")
    return row


# ---------- trang sản phẩm ----------
PRICE_RANGES = {"0-5": ("Dưới 5 triệu", 0, 5_000_000), "5-10": ("5 - 10 triệu", 5_000_000, 10_000_000),
                "10-20": ("10 - 20 triệu", 10_000_000, 20_000_000), "20-": ("Trên 20 triệu", 20_000_000, 10**10)}
SORTS = {"popular": ("Phổ biến", "sold DESC"), "hot": ("Khuyến mãi HOT", "(old_price - price) * 1.0 / old_price DESC"),
         "price_asc": ("Giá Thấp - Cao", "price ASC"), "price_desc": ("Giá Cao - Thấp", "price DESC")}
STEP = 20


def qs(request: Request, set: dict | None = None, toggle: tuple | None = None, drop: list | None = None) -> str:
    """Dựng lại query string của trang danh sách, có thể đặt/bỏ/bật-tắt một tham số."""
    from urllib.parse import urlencode
    items = [(k, v) for k, v in request.query_params.multi_items() if k != "show"]
    for k in (drop or []):
        items = [(a, b) for a, b in items if a != k]
    for k, v in (set or {}).items():
        items = [(a, b) for a, b in items if a != k] + [(k, v)]
    if toggle:
        k, v = toggle
        if (k, v) in items:
            items.remove((k, v))
        else:
            items.append((k, v))
    return "/?" + urlencode(items) if items else "/"


import re as _re
import unicodedata as _ud

_IMG_EXT = (".webp", ".jpg", ".jpeg", ".png")
# slug mẫu máy -> tên file ảnh (khi tên file không trùng slug)
IMAGE_ALIASES = {
    "iphone-17-pro": "iphone-17-pro-256-gb", "iphone-17": "iphone_17_256gb-3_2", "iphone-18-pro": "iphone-18-pro-01",
    "galaxy-s26-ultra": "samsung-galaxy-s26-ultra-1", "galaxy-z-fold8": "samsung-galaxy-z-fold-8-violet-01",
    "reno16-f": "oppo-reno16-f-02_5",
}
# phụ kiện: dùng chung một ảnh đại diện cho cả danh mục
CATEGORY_IMAGES = {
    "Ốp lưng": "op-bao-da-sam-sung-s24", "Sạc & cáp": "pk-apple-cap-sac", "Tai nghe": "pk-apple-tai-nghe",
    "Pin dự phòng": "pin-du-phong-20000-mah", "Dán màn hình": "dan-man-hinh-iphone-15",
}


def _slug(text: str) -> str:
    text = _ud.normalize("NFD", text.replace("đ", "d").replace("Đ", "D").replace("+", " plus "))
    text = "".join(c for c in text if _ud.category(c) != "Mn").lower()
    return _re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def product_image(p) -> str | None:
    """Ảnh của sản phẩm trong static/products/: ưu tiên <sku>.*, sau đó <slug mẫu máy>.* (bỏ dung lượng/màu)."""
    folder = config.BASE_DIR / "static" / "products"
    have = {f.name.lower() for f in folder.iterdir()} if folder.exists() else set()
    model = _re.sub(r"\s+(\d+GB|1TB)$", "", p["name"].split(" - ")[0]) if p["category"] == "Điện thoại" else p["name"]
    slug = _slug(model)
    stems = [p["sku"].lower(), slug, IMAGE_ALIASES.get(slug, ""), CATEGORY_IMAGES.get(p["category"], "")]
    for stem in filter(None, stems):
        for ext in _IMG_EXT:
            if stem + ext in have:
                return f"/static/products/{stem}{ext}"
    if p["category"] == "Điện thoại":  # chưa có ảnh đúng mẫu: lấy ảnh điện thoại bất kỳ (cố định theo mẫu máy)
        skip = set(CATEGORY_IMAGES.values())
        pool = sorted(n for n in have if n.endswith(_IMG_EXT) and n.rsplit(".", 1)[0] not in skip)
        if pool:
            return "/static/products/" + pool[sum(map(ord, slug)) % len(pool)]
    return None


templates.env.globals["product_image"] = product_image
templates.env.globals["qs"] = qs
templates.env.globals.update(PRICE_RANGES=PRICE_RANGES, SORTS=SORTS)


@app.get("/")
def home(request: Request, q: str = "", category: str = "", brand: list[str] = Query([]), price: str = "",
         os: str = "", storage: int = 0, sort: str = "popular", show: int = Query(STEP, ge=STEP, le=400),
         db: sqlite3.Connection = Depends(get_db)):
    where, args = ["1=1"], []
    if q:
        where.append("(name LIKE ? OR brand LIKE ?)")
        args += [f"%{q}%"] * 2
    if category:
        where.append("category = ?")
        args.append(category)
    if brand:
        where.append(f"brand IN ({','.join('?' * len(brand))})")
        args += brand
    if price in PRICE_RANGES:
        where.append("price >= ? AND price < ?")
        args += list(PRICE_RANGES[price][1:])
    if os in ("iOS", "Android"):
        where.append("os = ?")
        args.append(os)
    if storage:
        where.append("storage_gb = ?")
        args.append(storage)
    order = SORTS.get(sort, SORTS["popular"])[1]
    cond = " AND ".join(where)
    total = db.execute(f"SELECT COUNT(*) FROM products WHERE {cond}", args).fetchone()[0]
    products = db.execute(f"SELECT * FROM products WHERE {cond} ORDER BY {order}, id LIMIT ?",
                          args + [show]).fetchall()
    brands = [r[0] for r in db.execute("SELECT brand FROM products GROUP BY brand ORDER BY COUNT(*) DESC")]
    categories = [r[0] for r in db.execute(
        "SELECT category FROM products GROUP BY category ORDER BY category = 'Điện thoại' DESC, category")]
    filtered = bool(q or category or brand or price or os or storage)
    return render(request, db, "index.html", products=products, categories=categories, brands=brands, q=q,
                  category=category, sel_brands=brand, price=price, os=os, storage=storage, sort=sort,
                  show=show, total=total, more=max(0, total - len(products)), filtered=filtered, step=STEP)


@app.get("/products/{product_id}")
def product_detail(request: Request, product_id: int, db: sqlite3.Connection = Depends(get_db)):
    p = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Không tìm thấy sản phẩm")
    return render(request, db, "product.html", p=p)


# ---------- giỏ hàng ----------
@app.post("/cart/add")
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


@app.get("/cart")
def cart_view(request: Request, db: sqlite3.Connection = Depends(get_db)):
    return render(request, db, "cart.html", **cart_totals(request, db))


@app.post("/cart/remove")
def cart_remove(request: Request, product_id: int = Form(...)):
    cart = request.session.get("cart", {})
    cart.pop(str(product_id), None)
    request.session["cart"] = cart
    flash(request, "Đã xoá sản phẩm khỏi giỏ hàng.")
    return RedirectResponse("/cart", status_code=303)


@app.post("/cart/clear")
def cart_clear(request: Request):
    request.session["cart"] = {}
    request.session.pop("coupon", None)
    return RedirectResponse("/cart", status_code=303)


@app.post("/cart/coupon")
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


@app.post("/cart/update")
def cart_update(request: Request, product_id: int = Form(...), qty: int = Form(...),
                db: sqlite3.Connection = Depends(get_db)):
    cart = request.session.get("cart", {})
    if str(product_id) in cart:
        stock = db.execute("SELECT stock FROM products WHERE id = ?", (product_id,)).fetchone()
        if qty <= 0 or not stock:
            cart.pop(str(product_id))
        else:
            cart[str(product_id)] = min(qty, 10, stock["stock"])
        request.session["cart"] = cart
    return RedirectResponse("/cart", status_code=303)


# ---------- đăng ký / đăng nhập ----------
@app.get("/login")
def login_page(request: Request, next: str = "/", db: sqlite3.Connection = Depends(get_db)):
    return render(request, db, "login.html", next=safe_next(next), error=None)


@app.post("/login")
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


@app.get("/register")
def register_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    return render(request, db, "register.html", error=None, form={})


@app.post("/register")
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
    n = db.execute("SELECT COALESCE(MAX(CAST(SUBSTR(id, 2) AS INTEGER)), 0) FROM customers").fetchone()[0] + 1
    cid = f"C{n:04d}"
    db.execute("INSERT INTO customers(id,email,password_hash,full_name,phone,address,tier,created_at) "
               "VALUES (?,?,?,?,?,?,?,?)",
               (cid, email, hash_password(password), full_name.strip(), phone.strip(), "", "standard",
                datetime.now().strftime("%Y-%m-%d")))
    request.session["cid"] = cid
    flash(request, "Tạo tài khoản thành công!")
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---------- thanh toán & đơn hàng ----------
@app.get("/checkout")
def checkout_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    t = cart_totals(request, db)
    if not t["lines"]:
        flash(request, "Giỏ hàng đang trống.", "warn")
        return RedirectResponse("/", status_code=303)
    return render(request, db, "checkout.html", payments=PAYMENT_LABEL, **t)


@app.post("/checkout")
def checkout(request: Request, ship_name: str = Form(...), ship_phone: str = Form(...),
             ship_address: str = Form(...), payment_method: str = Form("cod"),
             db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    t = cart_totals(request, db)
    lines, subtotal, fee, discount = t["lines"], t["subtotal"], t["fee"], t["discount"]
    if not lines:
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
    db.execute(
        "INSERT INTO orders(id,customer_id,status,total,shipping_fee,payment_method,ship_name,ship_phone,"
        "ship_address,placed_at,eta,tracking_code,discount,coupon_code) VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
        (oid, user["id"], "pending", t["total"], fee, payment_method, ship_name.strip(), ship_phone.strip(),
         ship_address.strip(), now.strftime("%Y-%m-%d %H:%M"), (now + timedelta(days=4)).strftime("%Y-%m-%d"),
         discount, t["coupon"]))
    db.executemany("INSERT INTO order_items(order_id,product_id,qty,unit_price) VALUES (?,?,?,?)",
                   [(oid, ln["product"]["id"], ln["qty"], ln["product"]["price"]) for ln in lines])
    if not user["address"]:
        db.execute("UPDATE customers SET address = ?, phone = COALESCE(NULLIF(phone, ''), ?) WHERE id = ?",
                   (ship_address.strip(), ship_phone.strip(), user["id"]))
    request.session["cart"] = {}
    request.session.pop("coupon", None)
    flash(request, f"Đặt hàng thành công! Mã đơn của bạn là {oid}.")
    return RedirectResponse(f"/orders/{oid}", status_code=303)


@app.get("/orders")
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


@app.get("/orders/{order_id}")
def order_detail(request: Request, order_id: str, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    order = own_order(request, db, order_id, user)
    return render(request, db, "order_detail.html", order=order, items=order_items(db, order_id))


@app.post("/orders/{order_id}/cancel")
def cancel_order(request: Request, order_id: str, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    order = own_order(request, db, order_id, user)
    if order["status"] not in ("pending", "confirmed"):
        flash(request, "Chỉ huỷ được đơn đang chờ xác nhận hoặc đã xác nhận.", "warn")
    else:
        db.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
        for it in order_items(db, order_id):  # hoàn tồn kho
            db.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (it["qty"], it["product_id"]))
        flash(request, f"Đã huỷ đơn {order_id}.")
    return RedirectResponse(f"/orders/{order_id}", status_code=303)


@app.post("/orders/{order_id}/advance")
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
        db.execute("UPDATE orders SET status = ?, tracking_code = ? WHERE id = ?", (nxt, tracking, order_id))
        flash(request, f"[Demo] Đơn {order_id} chuyển sang: {STATUS_LABEL[nxt]}.")
    return RedirectResponse(f"/orders/{order_id}", status_code=303)


# ---------- mua lại / yêu thích / tài khoản / chính sách ----------
@app.post("/orders/{order_id}/reorder")
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


@app.post("/wishlist/toggle")
def wishlist_toggle(request: Request, product_id: int = Form(...), next: str = Form("/"),
                    db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    if db.execute("DELETE FROM wishlist WHERE customer_id = ? AND product_id = ?", (user["id"], product_id)).rowcount:
        flash(request, "Đã bỏ khỏi danh sách yêu thích.")
    elif db.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
        db.execute("INSERT INTO wishlist(customer_id, product_id) VALUES (?, ?)", (user["id"], product_id))
        flash(request, "Đã thêm vào danh sách yêu thích ❤️")
    return back(next)


@app.get("/wishlist")
def wishlist_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    products = db.execute("SELECT p.* FROM products p JOIN wishlist w ON w.product_id = p.id "
                          "WHERE w.customer_id = ? ORDER BY p.id DESC", (user["id"],)).fetchall()
    return render(request, db, "wishlist.html", products=products)


@app.get("/account")
def account_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    stats = db.execute("SELECT COUNT(*) AS n, COALESCE(SUM(total), 0) AS spent FROM orders "
                       "WHERE customer_id = ? AND status NOT IN ('cancelled', 'returned')", (user["id"],)).fetchone()
    return render(request, db, "account.html", stats=stats)


@app.post("/account")
def account_update(request: Request, full_name: str = Form(...), phone: str = Form(""), address: str = Form(""),
                   db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return to_login(request)
    db.execute("UPDATE customers SET full_name = ?, phone = ?, address = ? WHERE id = ?",
               (full_name.strip() or user["full_name"], phone.strip(), address.strip(), user["id"]))
    flash(request, "Đã cập nhật thông tin tài khoản.")
    return RedirectResponse("/account", status_code=303)


@app.post("/account/password")
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


POLICIES = {
    "doi-tra": ("Chính sách đổi trả", [
        ("Thời hạn", "1 đổi 1 trong 30 ngày kể từ ngày nhận hàng nếu sản phẩm lỗi do nhà sản xuất."),
        ("Điều kiện", "Sản phẩm còn đầy đủ hộp, phụ kiện, hoá đơn; không trầy xước, vào nước hay can thiệp phần mềm."),
        ("Hoàn tiền", "Hoàn tiền trong 3–7 ngày làm việc về phương thức thanh toán ban đầu sau khi nhận lại hàng."),
        ("Huỷ đơn", "Đơn ở trạng thái Chờ xác nhận hoặc Đã xác nhận có thể huỷ tại trang Đơn hàng của tôi.")]),
    "bao-hanh": ("Chính sách bảo hành", [
        ("Điện thoại", "Bảo hành chính hãng 12 tháng tại trung tâm bảo hành của hãng."),
        ("Phụ kiện", "Bảo hành 6 tháng, đổi mới trong 15 ngày nếu lỗi."),
        ("Không bảo hành", "Hư hỏng do rơi vỡ, vào nước, tự ý sửa chữa hoặc hết thời hạn bảo hành.")]),
    "van-chuyen": ("Chính sách vận chuyển", [
        ("Phí vận chuyển", "Miễn phí cho đơn từ 500.000₫; dưới mức này phí cố định 30.000₫."),
        ("Thời gian giao", "Dự kiến 3–5 ngày làm việc kể từ khi đặt hàng."),
        ("Theo dõi đơn", "Mã vận đơn hiển thị trong chi tiết đơn hàng sau khi đơn chuyển sang Đang giao.")]),
    "tra-gop": ("Chính sách trả góp 0%", [
        ("Điều kiện", "Áp dụng cho điện thoại, thanh toán qua thẻ tín dụng của các ngân hàng liên kết."),
        ("Kỳ hạn", "3, 6, 9 hoặc 12 tháng, lãi suất 0%, không phí chuyển đổi."),
        ("Hồ sơ", "Chỉ cần CCCD và thẻ tín dụng, duyệt trong khoảng 5 phút.")]),
    "thanh-toan": ("Phương thức thanh toán", [
        ("COD", "Thanh toán tiền mặt khi nhận hàng."),
        ("Chuyển khoản", "Chuyển khoản ngân hàng theo hướng dẫn sau khi đặt hàng."),
        ("Mô phỏng", "Website demo — không phát sinh giao dịch thật.")]),
}
templates.env.globals["POLICIES"] = POLICIES


@app.get("/policy/{slug}")
def policy(request: Request, slug: str, db: sqlite3.Connection = Depends(get_db)):
    if slug not in POLICIES:
        raise HTTPException(404, "Không tìm thấy trang")
    title, sections = POLICIES[slug]
    return render(request, db, "policy.html", title=title, sections=sections, slug=slug)


# ---------- token cho chat widget ----------
@app.get("/api/chat-token")
def chat_token(request: Request, db: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return JSONResponse({"error": "not_logged_in"}, status_code=401, headers={"Cache-Control": "no-store"})
    return JSONResponse({"token": sign_chat_token(user["id"]), "expires_in": config.CHAT_TOKEN_TTL,
                         "customer_name": user["full_name"]}, headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.exception_handler(404)
def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": getattr(exc, "detail", "not_found")}, status_code=404)
    from db import connect
    conn = connect()
    try:
        return render(request, conn, "404.html", status_code=404, message=getattr(exc, "detail", "Không tìm thấy trang"))
    finally:
        conn.close()
