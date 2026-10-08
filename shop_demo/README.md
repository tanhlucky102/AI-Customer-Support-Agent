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
Điền `DEMO_SHOP_API_KEY`, `DEMO_SHOP_TOKEN_SECRET`, `DEMO_SHOP_SESSION_SECRET` trong `.env` (nếu trống sẽ dùng khoá dev).

## Tính năng web
Danh sách + tìm kiếm/lọc/sắp xếp sản phẩm, chi tiết, giỏ hàng, đặt hàng (trừ tồn kho), đăng ký/đăng nhập/đăng xuất,
"Đơn hàng của tôi" + chi tiết, huỷ đơn, nút **[Demo]** đẩy đơn sang trạng thái kế tiếp (tạo mã vận đơn) để thử chatbot.

## REST API cho Agent Server (hợp đồng C2) — `Authorization: Bearer <DEMO_SHOP_API_KEY>`
- `GET /api/v1/customers/{customer_id}`
- `GET /api/v1/customers/{customer_id}/orders?limit=&status=`
- `GET /api/v1/customers/{customer_id}/orders/{order_id}`

Thiếu/sai key → 401. Đơn không tồn tại hoặc không thuộc khách → 404 (giống nhau).

## Nhúng chatbot
- `GET /api/chat-token` (cần đăng nhập, nếu không → 401): JWT HS256 `{iss, sub, aud="ati-agent", iat, exp, jti}` (C3).
  `security.sign_chat_token` là bản tạm, thay bằng `sdk.sign_user_token` của M1 khi có.
- `base.html` chèn `<script src="{AGENT_SERVER_URL}/widget/loader.js" data-shop-key="pk_demo_shop" data-token-url="/api/chat-token">`;
  nút Đăng xuất gọi `ATI.reset()`. Tắt widget: `SHOW_WIDGET=0`.
