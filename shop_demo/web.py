"""Phần dùng chung của các trang: template Jinja, nhãn trạng thái, khách đang đăng nhập, giỏ hàng."""
import re as _re
import sqlite3
import unicodedata as _ud

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from offers import COUPONS, EVENT_BANNER_TITLE, coupon_discount, event_discount

PAGE_SIZE = 12
SHIPPING_FREE_FROM = 500_000
SHIPPING_FEE = 30_000

STATUS_LABEL = {
    "pending": "Chờ xác nhận", "confirmed": "Đã xác nhận", "shipping": "Đang giao",
    "delivered": "Đã giao", "cancelled": "Đã huỷ", "returned": "Đã hoàn trả",
}
NEXT_STATUS = {"pending": "confirmed", "confirmed": "shipping", "shipping": "delivered"}
PAYMENT_LABEL = {"cod": "Thanh toán khi nhận hàng (COD)", "bank": "Chuyển khoản ngân hàng"}

templates = Jinja2Templates(directory=config.BASE_DIR / "templates")
templates.env.filters["vnd"] = lambda v: f"{int(v):,}".replace(",", ".") + " ₫"
templates.env.filters["status_label"] = lambda s: STATUS_LABEL.get(s, s)
templates.env.filters["payment_label"] = lambda s: PAYMENT_LABEL.get(s, s)
CAT_ICON = {"Điện thoại": "📱", "Ốp lưng": "🛡️", "Sạc & cáp": "🔌", "Tai nghe": "🎧", "Pin dự phòng": "🔋",
            "Dán màn hình": "✨"}
templates.env.globals.update(STATUS_LABEL=STATUS_LABEL, NEXT_STATUS=NEXT_STATUS, cfg=config,
                             CAT_ICON=CAT_ICON, EVENT_BANNER_TITLE=EVENT_BANNER_TITLE)


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
    path = request.url.path
    if request.method != "GET":  # sau đăng nhập, quay về trang đang xem, không phải URL chỉ nhận POST
        referer = request.headers.get("referer", "")
        own = f"{request.url.scheme}://{request.url.netloc}"
        path = referer[len(own):] if referer.startswith(own + "/") else "/"
    return RedirectResponse(f"/login?next={safe_next(path)}", status_code=303)


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


def cart_totals(request: Request, db: sqlite3.Connection, payment_method: str | None = None) -> dict:
    lines, subtotal = cart_lines(request, db)
    event = event_discount(lines, payment_method)  # the events running now (events.json "rule")
    fee = 0 if event["free_shipping"] else shipping_fee(subtotal)
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
            "event_discount": event["amount"], "events": event["titles"], "event_hints": event["hints"],
            "gifts": event["gifts"], "total": max(0, subtotal + fee - discount - event["amount"]),
            "free_from": SHIPPING_FREE_FROM, "coupons": COUPONS}


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


# ---------- danh sách sản phẩm ----------
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


templates.env.globals.update(product_image=product_image, qs=qs, PRICE_RANGES=PRICE_RANGES, SORTS=SORTS)
