# Shop demo — Demo Electronics

Website mô phỏng dùng để test framework chatbot (vai trò M3).

## Chạy
```
cd shop_demo
pip install -r requirements.txt
python seed.py --reset        # 300 khách, 200 sản phẩm, 1.000 đơn
uvicorn app:app --port 8001 --reload
```
Mở http://localhost:8001 — đăng nhập `an@demo.vn` / `demo1234` (có đơn `SO10293`), hoặc `binh@demo.vn`.
Điền `DEMO_SHOP_API_KEY`, `DEMO_SHOP_TOKEN_SECRET`, `DEMO_SHOP_SESSION_SECRET` trong `.env`. Nếu trống, server sinh khoá ngẫu nhiên cho mỗi lần chạy: website vẫn dùng được nhưng Agent Server không gọi được REST API, không xác minh được token chat, và phiên đăng nhập mất khi khởi động lại.

## Tính năng web
Danh sách + tìm kiếm/lọc/sắp xếp sản phẩm, chi tiết, giỏ hàng, đặt hàng (trừ tồn kho), đăng ký/đăng nhập/đăng xuất,
"Đơn hàng của tôi" + chi tiết, huỷ đơn, nút **[Demo]** đẩy đơn sang trạng thái kế tiếp (tạo mã vận đơn) để thử chatbot.

## REST API cho Agent Server (hợp đồng C2) — `Authorization: Bearer <DEMO_SHOP_API_KEY>`
- `GET /api/v1/customers/{customer_id}`
- `GET /api/v1/customers/{customer_id}/orders?limit=&status=`
- `GET /api/v1/customers/{customer_id}/orders/{order_id}`
- `GET /api/v1/products?q=&category=&max_price=&limit=` (dữ liệu sản phẩm cho chatbot)
- `GET /api/v1/offers` (mã checkout đang chấp nhận, sản phẩm giảm giá còn hàng, lịch sự kiện xác nhận)

Mã giảm giá ở `offers.py` là cùng dữ liệu mà checkout sử dụng. Sự kiện đọc từ `events.json`: mỗi sự kiện có `title`, `description`, `starts_at`, `ends_at` (ISO 8601 có múi giờ), `url` nội bộ và `rule` là luật checkout áp dụng (`brand`, `categories`, `percent`, `max_discount`, `payment`, `free_shipping`, `gift`), nên điều trợ lý báo cho khách đúng bằng số tiền khách được trừ (`offers.event_discount`, test `test_offers.py`). Ưu đãi chỉ áp dụng khi chuyển khoản thì giỏ hàng nhắc khách. Không dùng chữ trên banner làm bằng chứng cho mức giảm hay ngày diễn ra.

Đơn có `delivered_at` (ngày giao thật): nút [Demo] ghi ngày khi đơn chuyển sang "Đã giao"; đơn cũ được điền khi khởi động (`db.backfill_delivered`).

Code: `app.py` dựng ứng dụng; `web.py` phần dùng chung (template, giỏ hàng); `pages_catalog.py`, `pages_cart.py`, `pages_orders.py`, `pages_account.py` là các trang; `api.py` là API C2. Test: `python -m unittest discover -s . -p "test_*.py"` (lệnh test chính ở thư mục gốc cũng chạy các test này).

Thiếu/sai key → 401. Đơn không tồn tại hoặc không thuộc khách → 404 (giống nhau).

## Nhúng chatbot
- Widget từ `agent_server/` dùng AI Box (khi có key), FAQ, API sản phẩm/đơn hàng và lưu lịch sử theo khách. Cần đăng nhập trước khi hỏi. Xem [hướng dẫn Agent Server](../agent_server/README.md). Đặt `SHOW_WIDGET=0` để tắt widget.
- `GET /api/chat-token` (cần đăng nhập, nếu không → 401): JWT HS256 `{iss, sub, aud="ati-agent", iat, exp, jti}` (C3).
  `security.sign_chat_token` gọi `sign_user_token` trong `sdk/ati_sdk.py` của framework.
- Trang chèn `<script src="{AGENT_SERVER_URL}/widget/loader.js" data-shop-key="pk_demo_shop" data-token-url="/api/chat-token">`;
  nút Đăng xuất gọi `ATI.reset()`.
