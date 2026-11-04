"""Website shop demo — "khách hàng" của framework chatbot.

Chạy:  python seed.py && uvicorn app:app --port 8001 --reload   (trong thư mục shop_demo/)

web.py           template Jinja, nhãn, khách đang đăng nhập, giỏ hàng (dùng chung)
pages_catalog.py trang chủ, sản phẩm, yêu thích, trung tâm trợ giúp
pages_cart.py    giỏ hàng, mã giảm giá, thanh toán
pages_orders.py  đơn hàng của tôi
pages_account.py đăng ký, đăng nhập/đăng xuất, tài khoản, token chat
api.py           API cho Agent Server (hợp đồng C2)
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import config
import pages_account
import pages_cart
import pages_catalog
import pages_orders
from api import router as api_router
from db import init_db
from web import render


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if config.MISSING_KEYS:
        print("[shop_demo] WARNING: " + ", ".join(config.MISSING_KEYS) + " missing in .env; using random "
              "per-process keys, so the Agent Server cannot call this API or verify chat tokens.")
    yield


app = FastAPI(title="Demo Mobile", lifespan=lifespan)
# Tên cookie riêng: cookie không phân biệt cổng, nên hai shop demo cùng chạy trên localhost với tên
# mặc định "session" sẽ ghi đè đăng nhập của nhau.
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET, same_site="lax",
                   https_only=False, max_age=60 * 60 * 24 * 7,
                   session_cookie="demo_mobile_session")  # cookie HttpOnly theo mặc định


@app.middleware("http")
async def same_origin_forms(request: Request, call_next):
    """Form POST chỉ nhận từ chính website (chống CSRF, kể cả "login CSRF"); trang HTML không được
    cache, để bấm Back sau khi đăng xuất không hiện lại thông tin tài khoản."""
    if request.method == "POST" and not request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        own = {f"{request.url.scheme}://{request.url.netloc}", config.SHOP_URL}
        if origin and origin not in own:
            return JSONResponse({"detail": "cross_site_request"}, status_code=403)
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=config.BASE_DIR / "static"), name="static")
app.include_router(api_router)
for pages in (pages_catalog, pages_cart, pages_orders, pages_account):
    app.include_router(pages.router)


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
