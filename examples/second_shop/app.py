"""Phụ Kiện Xanh: a second, independent shop used to show integration level 1.

Its order API was NOT written for our framework: different paths (/v2/...), an X-Api-Key header,
its own field names (code, state, amount.value, lines[].title) and status values (HANDED_TO_GHTK).
The Agent Server reads it through commerce.json, a declarative mapping, with no shop-specific code.
It has no product-search endpoint, so the agent is simply not offered that tool for this shop.

    uvicorn examples.second_shop.app:app --port 8003      (from the repository root)
Demo login: chau@pkx.vn / demo1234 (has order PKX204518) or duy@pkx.vn / demo1234.
"""

import hmac
import html
import re
import secrets
import sys
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sdk"))
from ati_sdk import sign_user_token  # noqa: E402
from shared.settings import env  # noqa: E402

SHOP_ID = "second_shop"
SHOP_URL = env("SECOND_SHOP_URL", "http://localhost:8003").rstrip("/")
AGENT_URL = env("AGENT_SERVER_URL", "http://localhost:8000").rstrip("/")
API_KEY = env("SECOND_SHOP_API_KEY")
TOKEN_SECRET = env("SECOND_SHOP_TOKEN_SECRET")
PUBLIC_KEY = env("SECOND_SHOP_PUBLIC_KEY")
KB_DIR = ROOT / "data" / "kb" / SHOP_ID
TODAY = date.today()

USERS = {
    "K1001": {"uid": "K1001", "displayName": "Lê Minh Châu", "mail": "chau@pkx.vn",
              "mobile": "0938123456", "shipTo": "21 Phan Xích Long, Phú Nhuận, TP. Hồ Chí Minh",
              "level": "Thành viên", "since": "2025-11-02", "points": 340},
    "K1002": {"uid": "K1002", "displayName": "Phạm Quốc Duy", "mail": "duy@pkx.vn",
              "mobile": "0977654321", "shipTo": "5 Kim Mã, Ba Đình, Hà Nội",
              "level": "Thành viên", "since": "2026-02-14", "points": 120},
}
PASSWORD = "demo1234"


def _order(code, uid, state, days_ago, lines, ghtk=None, pay="COD"):
    subtotal = sum(line["price"] * line["quantity"] for line in lines)
    fee = 0 if subtotal >= 300_000 else 25_000
    created = TODAY - timedelta(days=days_ago)
    return {"code": code, "uid": uid, "state": state, "createdAt": created.isoformat(),
            "expectedBy": (created + timedelta(days=4)).isoformat(), "ghtkCode": ghtk, "pay": pay,
            "shipFee": fee, "amount": {"value": subtotal + fee, "currency": "VND"}, "lines": lines}


ORDERS = {order["code"]: order for order in [
    _order("PKX204518", "K1001", "HANDED_TO_GHTK", 3,
           [{"title": "Sạc nhanh GaN 65W 3 cổng", "quantity": 1, "price": 590_000}], "S22.81726354", "MOMO"),
    _order("PKX203977", "K1001", "DELIVERED", 21,
           [{"title": "Cáp USB-C to USB-C 1m bện dù", "quantity": 2, "price": 129_000}], "S22.80011873"),
    _order("PKX205102", "K1001", "PLACED", 0,
           [{"title": "Ốp lưng silicon iPhone 16", "quantity": 1, "price": 149_000}]),
    _order("PKX204860", "K1002", "PACKING", 1,
           [{"title": "Tai nghe Bluetooth chống ồn", "quantity": 1, "price": 890_000}], None, "MOMO"),
    _order("PKX202231", "K1002", "CANCELED", 40,
           [{"title": "Kính cường lực Galaxy S25", "quantity": 1, "price": 99_000}]),
]}

app = FastAPI(title="Phụ Kiện Xanh (second demo shop)")
# Never a published default: without a configured secret, sessions use a per-process random key.
app.add_middleware(SessionMiddleware, secret_key=env("SECOND_SHOP_SESSION_SECRET") or secrets.token_urlsafe(32),
                   https_only=False, same_site="lax")


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    if not API_KEY or not x_api_key or not hmac.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "bad api key")


# ---- Shop's own order API (not designed for the framework) -------------------------------

@app.get("/v2/orders/{code}", dependencies=[Depends(require_key)])
def api_order(code: str, customer: str):
    order = ORDERS.get(code.upper())
    if not order or order["uid"] != customer:  # Someone else's order looks the same as none.
        raise HTTPException(404, "order not found")
    return {"data": {"order": order}}


@app.get("/v2/users/{uid}/orders", dependencies=[Depends(require_key)])
def api_orders(uid: str, limit: int = Query(5, alias="max", ge=1, le=20), state: str | None = None):
    rows = sorted((o for o in ORDERS.values() if o["uid"] == uid and (not state or o["state"] == state)),
                  key=lambda o: o["createdAt"], reverse=True)
    return {"data": {"orders": rows[:limit]}}


@app.get("/v2/users/{uid}", dependencies=[Depends(require_key)])
def api_user(uid: str):
    if uid not in USERS:
        raise HTTPException(404, "user not found")
    return {"data": {"user": USERS[uid]}}


# ---- Minimal website ---------------------------------------------------------------------

def page(title: str, body: str, user: dict | None) -> HTMLResponse:
    account = (f'Xin chào {html.escape(user["displayName"])} · <a href="/account/orders">Đơn hàng</a> · '
               '<form method="post" action="/logout" style="display:inline"><button>Đăng xuất</button></form>'
               if user else '<a href="/login">Đăng nhập</a>')
    widget = ""
    if PUBLIC_KEY:
        widget = (f'<script src="{AGENT_URL}/widget/loader.js" data-shop-key="{html.escape(PUBLIC_KEY)}" '
                  f'data-token-url="/api/chat-token" data-login-url="/login"></script>')
        if not user:
            widget += '<script>window.addEventListener("load",function(){window.ATI&&ATI.reset();});</script>'
    return HTMLResponse(f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · Phụ Kiện Xanh</title>
<style>body{{font-family:system-ui,sans-serif;margin:0;color:#14332a}}header{{background:#0f7a5c;color:#fff;
padding:14px 20px;display:flex;justify-content:space-between;align-items:center}}header a,header button{{color:#fff;
background:none;border:0;font:inherit;cursor:pointer;text-decoration:underline}}main{{max-width:820px;margin:24px auto;
padding:0 16px;line-height:1.55}}table{{border-collapse:collapse}}td,th{{border:1px solid #cde3da;padding:6px 10px}}
nav a{{margin-right:12px}}</style></head><body><header><strong>Phụ Kiện Xanh</strong><span>{account}</span></header>
<main><nav><a href="/">Trang chủ</a>{''.join(f'<a href="/help/{p.stem}">{html.escape(_title(p))}</a>' for p in sorted(KB_DIR.glob("*.md")))}</nav>
{body}</main>{widget}</body></html>""")


def _title(path: Path) -> str:
    match = re.search(r"^title:\s*(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1).strip() if match else path.stem


def current_user(request: Request) -> dict | None:
    return USERS.get(request.session.get("uid", ""))


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return page("Trang chủ", "<h1>Phụ kiện điện thoại chính hãng</h1><p>Sạc, cáp, tai nghe, ốp lưng. "
                "Miễn phí giao hàng cho đơn từ 300.000₫. Hỏi trợ lý ở góc phải màn hình.</p>",
                current_user(request))


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return page("Đăng nhập", """<h1>Đăng nhập</h1><form method="post"><p><input name="email"
        placeholder="Email" value="chau@pkx.vn"></p><p><input name="password" type="password"
        placeholder="Mật khẩu" value="demo1234"></p><button>Đăng nhập</button></form>""",
                current_user(request))


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = next((u for u in USERS.values() if u["mail"] == email.strip().lower()), None)
    if not user or not hmac.compare_digest(password, PASSWORD):
        return RedirectResponse("/login", status_code=303)
    request.session["uid"] = user["uid"]
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/api/chat-token")
def chat_token(request: Request):
    user = current_user(request)
    if not user or not TOKEN_SECRET:
        raise HTTPException(401, "login required")
    return {"token": sign_user_token(user["uid"], shop_id=SHOP_ID, secret=TOKEN_SECRET),
            "customer_name": user["displayName"]}


@app.get("/account/orders", response_class=HTMLResponse)
def my_orders(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    rows = "".join(f'<tr><td><a href="/account/orders/{o["code"]}">{o["code"]}</a></td><td>{o["state"]}</td>'
                   f'<td>{o["amount"]["value"]:,}₫</td></tr>'.replace(",", ".")
                   for o in ORDERS.values() if o["uid"] == user["uid"])
    return page("Đơn hàng", f"<h1>Đơn hàng của tôi</h1><table><tr><th>Mã</th><th>Trạng thái</th>"
                f"<th>Tổng</th></tr>{rows}</table>", user)


@app.get("/account/orders/{code}", response_class=HTMLResponse)
def order_page(code: str, request: Request):
    user = current_user(request)
    order = ORDERS.get(code.upper())
    if not user or not order or order["uid"] != user["uid"]:
        raise HTTPException(404)
    lines = "".join(f"<li>{html.escape(line['title'])} × {line['quantity']}</li>" for line in order["lines"])
    return page(order["code"], f"<h1>Đơn {order['code']}</h1><p>Trạng thái: {order['state']}<br>"
                f"Mã vận đơn GHTK: {order['ghtkCode'] or 'chưa có'}</p><ul>{lines}</ul>", user)


@app.get("/help/{slug}", response_class=HTMLResponse)
def help_page(slug: str, request: Request):
    path = KB_DIR / f"{slug}.md"
    if not re.fullmatch(r"[a-z0-9-]+", slug) or not path.exists():
        raise HTTPException(404)
    text = re.sub(r"\A---.*?---\s*", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    body, section = [], 0
    for line in text.splitlines():
        if line.startswith("# "):
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f'<h2 id="muc-{section}">{html.escape(line[3:])}</h2>')
            section += 1
        elif line.strip():
            body.append(f"<p>{html.escape(line)}</p>")
    return page(_title(path), "".join(body), current_user(request))
