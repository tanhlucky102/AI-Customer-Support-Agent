# Widget nhúng + Agent Server giả lập

Đây là bản thử độc lập để kiểm tra cách một website nhúng widget và gọi Chat API ở origin khác. **Khách phải đăng nhập trước khi hỏi.** Khi có `AI_BOX_API_KEY`, server gọi model `ds/deepseek-flash` qua AI Box để hiểu ý định và chọn FAQ hoặc hướng tra sản phẩm/đơn hàng. Nội dung trả lời lấy từ FAQ trong SQLite và REST API của `shop_demo`. Hội thoại được lưu trong SQLite theo khách hàng, mở lại được sau khi khởi động lại server. Khi không có key hoặc AI Box tạm lỗi, server dùng bộ so khớp quy tắc cũ. Đây chưa phải Agent Core trên máy thành viên nhóm và chưa có RAG. Xem [yêu cầu chức năng](REQUIREMENTS.md).

## Mô hình triển khai

```
Backend website ── PUT /v1/shops/{shop_id}/faqs hoặc /offers ──> Agent Server ──> SQLite theo shop
Website (8001 hoặc 8002) ── tải /widget/loader.js ──> Agent Server (8000)
                         └─ iframe /widget/frame ───> POST /v1/chat (JWT, SSE)
Shop demo /api/chat-token ── JWT qua postMessage ──> iframe
Agent Server ── AI Box Chat Completions ──> Phân loại ý định/chọn ID FAQ
Agent Server ── SQLite ──> Hội thoại theo shop và khách
Agent Server ── REST + API key ──> Shop demo /api/v1/customers/..., /products và /offers
```

Luồng thử tương ứng với Chatbase: **Build** (cấp shop, đẩy FAQ qua API) → **Test** (hỏi trên shop demo và website mẫu) → **Deploy** (một thẻ script) → **Optimize** (nút phản hồi 👍/👎, thống kê tạm trong bộ nhớ tại `/v1/demo-stats`). Đây chỉ là mô phỏng các bước liên quan đến widget, không phải bản sao các tính năng quản trị của Chatbase.

## Chạy trên cùng máy

Tại thư mục gốc repo, tạo môi trường Python và cài dependency (nếu `.venv` đã có thì bỏ qua dòng đầu):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r shop_demo\requirements.txt -r agent_server\requirements.txt
```

**Terminal 1 — website shop:**

```powershell
$env:WIDGET_MODE = 'external'
$env:AGENT_SERVER_URL = 'http://localhost:8000'
cd shop_demo
..\.venv\Scripts\python.exe seed.py                # chỉ chạy nếu chưa có shop.db
..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8001
```

**Terminal 2 — Agent Server giả lập:**

```powershell
cd agent_server
..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Mở `http://localhost:8001`. Đăng nhập `an@demo.vn` / `demo1234`, rồi hỏi "Chào shop", "Shop đang có sale gì không?", "Shop có sự kiện gì sắp tới?", "Phí giao hàng là bao nhiêu?", "Đơn SO10293 của tôi" hoặc "iPhone 16 Pro Max 256GB còn hàng không?". Nút **Chat mới** tạo cuộc trò chuyện khác; **Lịch sử** mở lại những cuộc trò chuyện cũ. Nút 👍/👎 ghi bộ đếm tạm; mở `http://localhost:8000/v1/demo-stats` để xem.

## Bật AI Box

Tạo `.env` ở thư mục gốc (file này đã nằm trong `.gitignore`):

```dotenv
AI_BOX_BASE_URL=https://api.ai-box.vn/v1
AI_BOX_MODEL=ds/deepseek-flash
AI_BOX_API_KEY=<key của bạn>
WIDGET_MODE=external
AGENT_SERVER_URL=http://localhost:8000
```

Khởi động lại **cả shop demo và Agent Server** sau khi đổi `.env`. `GET /healthz` trả `mode: llm` khi key được nạp. Có thể thử hỏi "Mua món rẻ có tốn tiền ship không?", "Xem giúp đơn 10293", "Điện thoại nào dưới 10 triệu?", hoặc "Đơn tôi mua tuần trước tới đâu?". Câu cuối được hiểu là yêu cầu xem các đơn gần đây để khách chọn mã; server chưa tìm chính xác theo mô tả sản phẩm/ngày mua. Câu hỏi không có dữ liệu phù hợp được trả lời là chưa xác định và hướng liên hệ trực tiếp shop. Nếu AI Box lỗi, sự kiện `done` của lượt chat trả `mode: fallback`.

Server gửi câu hỏi, tối đa sáu tin nhắn gần nhất của cùng hội thoại và nội dung FAQ liên quan của đúng shop cho model (có giới hạn kích thước). Key chỉ nằm ở backend. Model không nhận JWT, khóa API shop hay quyền chọn `customer_id`; mã đơn được đối chiếu với chính câu người dùng nhập trước khi gọi shop API. Câu trả lời hiện được chia thành các đoạn SSE sau khi xử lý xong, chưa truyền token trực tiếp từ model.

Trang tĩnh `examples/third_party_site` vẫn cho thấy cách tải widget từ origin khác, nhưng **không thể gửi chat** vì không có đăng nhập và `/api/chat-token`. Để dùng thật trên website thứ hai, backend của website đó phải cấp JWT cho khách đã đăng nhập theo [hướng dẫn tích hợp](FAQ_INTEGRATION.md).

## Website khách đẩy FAQ, ưu đãi và sự kiện lên dịch vụ

Xem [FAQ_INTEGRATION.md](FAQ_INTEGRATION.md) để lấy JSON mẫu, lệnh cấp shop, lệnh đồng bộ và mã nhúng. Cùng khóa ghi backend dùng cho FAQ và bản ưu đãi/sự kiện. Trình duyệt chỉ nhận `public_key`. Sau khi đồng bộ, câu hỏi FAQ/ưu đãi/sự kiện chỉ đọc bản lưu trên Agent Server, không gọi API nội dung của khách theo từng lượt chat.

## Chạy khi Agent Server ở máy thành viên khác

`localhost` trên máy bạn không phải `localhost` trên máy bạn ấy. Agent Server cần một URL HTTPS mà trình duyệt người dùng truy cập được. Khi thử có thể dùng HTTPS tunnel tạm; đặt `AGENT_SERVER_URL` của shop demo thành URL đó. Server cũng cần `WIDGET_ALLOWED_ORIGINS` gồm origin thực của website nhúng, thí dụ `https://shop.example.vn`.

Nếu thử tra đơn, Agent Server còn phải truy cập được API của shop demo. Đặt `DEMO_SHOP_API_URL` trên máy chạy Agent Server thành URL HTTPS của shop (hoặc địa chỉ mạng riêng mà hai máy kết nối được). `DEMO_SHOP_URL` là URL mà trình duyệt mở các liên kết nguồn. Hai máy cần cùng `DEMO_SHOP_TOKEN_SECRET`; Agent Server cần `DEMO_SHOP_API_KEY` trùng với shop. Giữ hai khóa này ở file `.env` riêng trên từng máy, không đặt trong script nhúng hoặc commit lên Git.

Khi website chạy HTTPS, script và iframe cũng cần URL HTTPS. Website có CSP chặt phải cho phép origin của Agent Server trong `script-src`, `style-src` và `frame-src`. Server chỉ cho các origin trong `WIDGET_ALLOWED_ORIGINS` nhúng iframe qua `frame-ancestors`.

## Mã nhúng cho website khác

```html
<script src="https://agent.example.vn/widget/loader.js"
        data-shop-key="pk_demo_shop"
        data-token-url="/api/chat-token" async></script>
```

`data-token-url` là **bắt buộc để gửi câu hỏi**; đó phải là endpoint **cùng origin** với website, trả `{ "token": "...", "customer_name": "..." }` theo session đăng nhập. JWT được ký bằng khóa riêng của shop do lệnh cấp shop tạo. Widget không đưa API key hoặc khóa ký vào trình duyệt. Khi khách đăng xuất, trang gọi `ATI.reset()` để xóa nội dung và token đang hiển thị; lịch sử trên server vẫn gắn với tài khoản để mở lại sau.

## Hợp đồng API thử nghiệm

| Endpoint | Vai trò |
|---|---|
| `GET /healthz` | Kiểm tra server, trả `mode: llm` hoặc `mock` và model đang cấu hình |
| `GET /v1/widget-config?shop_key=...` | Tên, màu, câu chào, gợi ý |
| `POST /v1/chat` | Nhận `shop_key`, `message`, `conversation_id`; trả `text/event-stream` |
| `GET /v1/conversations?shop_key=...` | Danh sách hội thoại của khách đăng nhập |
| `GET /v1/conversations/{id}?shop_key=...` | Tin nhắn trong một hội thoại thuộc khách đó |
| `PUT /v1/shops/{shop_id}/faqs` | Backend khách thay toàn bộ FAQ của shop (Bearer write key) |
| `GET /v1/shops/{shop_id}/faqs` | Backend khách kiểm tra bản FAQ đã lưu (Bearer write key) |
| `PUT /v1/shops/{shop_id}/offers` | Backend khách thay toàn bộ ưu đãi/sự kiện đã công bố (Bearer write key) |
| `GET /v1/shops/{shop_id}/offers` | Backend khách kiểm tra bản ưu đãi/sự kiện đã lưu (Bearer write key) |
| `POST /v1/feedback` | Ghi 👍/👎 trong bộ nhớ |
| `GET /v1/demo-stats` | Xem bộ đếm phản hồi của bản thử |

Các SSE event: `status` (`message`), `token` (`text`), `citations` (`sources: [{label, href}]`), `done` (`conversation_id`, `mode`), `error` (`message`). Mọi lệnh chat và đọc lịch sử đều cần `Authorization: Bearer <JWT>`; server kiểm tra chữ ký, hạn dùng, `iss` và `aud`, rồi lấy `customer_id` từ `sub`. Dữ liệu đơn hàng được lấy từ shop API theo `sub`, không lấy ID khách do trình duyệt gửi.

## Kiểm thử lại

Trong `agent_server/`, chạy `..\.venv\Scripts\python.exe -m unittest -v test_mock_server` để kiểm tra hợp đồng API và quyền tra đơn. Khi cả hai server đang chạy, dùng `..\.venv\Scripts\python.exe smoke_live.py` để thử kết nối thật. Nếu máy có Chrome, `node browser_smoke.mjs` mở trình duyệt headless, bấm câu hỏi gợi ý và lưu ảnh chụp vào thư mục tạm của Windows.

**Giới hạn:** khách cần chủ động gọi API khi FAQ hoặc ưu đãi/sự kiện thay đổi; chưa có lịch tự kéo dữ liệu, Agent Core của nhóm, RAG, ticket, giới hạn tốc độ hay lưu phản hồi bền vững. Chế độ AI hiện phân loại ý định, chọn FAQ và thông số tìm sản phẩm; bản demo có API sản phẩm/đơn hàng/ưu đãi của `shop_demo`. Server trên máy cá nhân/tunnel tạm chỉ dành cho thử nghiệm.
