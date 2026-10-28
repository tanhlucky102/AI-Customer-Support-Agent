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
- `GET /api/v1/products?q=&category=&max_price=&limit=` (dữ liệu sản phẩm cho chatbot)
- `GET /api/v1/offers` (mã checkout đang chấp nhận, sản phẩm giảm giá còn hàng, lịch sự kiện xác nhận)

Mã giảm giá ở `offers.py` là cùng dữ liệu mà checkout sử dụng. Lịch sự kiện đọc từ `events.json`; ban đầu `events` rỗng nên chatbot chỉ thấy banner trên trang chủ và nói rõ **chưa có thời gian/điều kiện sự kiện được xác nhận**. Muốn công bố sự kiện thật, thêm `title`, `description`, `starts_at`, `ends_at` (ISO 8601 có múi giờ) và `url` nội bộ vào `events.json`, rồi kiểm tra API. Không dùng chữ trên banner làm bằng chứng cho mức giảm hay ngày diễn ra.

Thiếu/sai key → 401. Đơn không tồn tại hoặc không thuộc khách → 404 (giống nhau).

## Nhúng chatbot
- `WIDGET_MODE=external`: widget từ `agent_server/` dùng AI Box (khi có key), FAQ, API sản phẩm/đơn hàng và lưu lịch sử theo khách. Cần đăng nhập trước khi hỏi. Xem [hướng dẫn Agent Server](../agent_server/README.md).
- `WIDGET_MODE=demo` là chatbot quy tắc cũ ngay trên website, cũng yêu cầu đăng nhập nhưng không có AI và lịch sử. Đặt `SHOW_WIDGET=0` để tắt widget.
- `GET /api/chat-token` (cần đăng nhập, nếu không → 401): JWT HS256 `{iss, sub, aud="ati-agent", iat, exp, jti}` (C3).
  `security.sign_chat_token` là bản tạm, thay bằng `sdk.sign_user_token` của M1 khi có.
- Chế độ `external` chèn `<script src="{AGENT_SERVER_URL}/widget/loader.js" data-shop-key="pk_demo_shop" data-token-url="/api/chat-token">`;
  nút Đăng xuất gọi `ATI.reset()`.
