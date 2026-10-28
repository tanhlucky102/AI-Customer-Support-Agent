# Tích hợp FAQ, ưu đãi và sự kiện cho website khách hàng

Framework ở đây đóng vai trò **dịch vụ bên thứ ba**. Backend website khách gửi bản FAQ lên Agent Server khi nội dung thay đổi. Agent Server lưu dữ liệu theo `shop_id`; widget trên website gửi câu hỏi đến `/v1/chat`, và server trả lời từ dữ liệu đã lưu. **Mỗi câu hỏi FAQ không gọi ngược API FAQ của website khách.**

## 1. Nhận thông tin tích hợp

Đội vận hành cấp một shop bằng lệnh dưới đây trong thư mục `agent_server/`:

```powershell
..\.venv\Scripts\python.exe provision_shop.py --shop-id sample_store --shop-url http://localhost:8002 --origin http://127.0.0.1:8002 --title "Trợ lý Sample Store" --suggestion "Giờ mở cửa?" --suggestion "Làm sao liên hệ hỗ trợ?"
```

Lệnh in ra `shop_id`, `public_key`, `faq_write_key`, `chat_token_secret` **một lần**. Đội vận hành đưa hai khóa bí mật cho **backend** khách qua kênh bảo mật; `public_key` dùng công khai trong mã nhúng. Bản demo `demo_shop` đã được tạo sẵn với `public_key=pk_demo_shop`. Nếu muốn đồng bộ FAQ cho demo shop, đặt `DEMO_FAQ_WRITE_KEY` trong `.env`; khi biến này trống, không có khóa ghi demo được công bố.

Shop được cấp từ phiên bản cũ chưa có `chat_token_secret`: trong `agent_server/`, chạy `..\.venv\Scripts\python.exe rotate_chat_secret.py --shop-id <shop_id>` để cấp khóa. Lệnh cũng dùng để xoay khóa; JWT cũ sẽ hết hiệu lực ngay. Chuyển khóa mới cho backend khách qua kênh bảo mật và không lưu vào Git.

## 2. Backend khách đồng bộ FAQ

Backend khách chuẩn bị JSON như `examples/third_party_site/faqs.json`:

```json
{
  "faqs": [
    {
      "id": "opening-hours",
      "question": "Website mở cửa lúc nào?",
      "answer": "Hỗ trợ từ 8:00 đến 17:00, thứ Hai đến thứ Sáu.",
      "keywords": ["giờ mở cửa", "giờ hỗ trợ"],
      "source_url": "https://shop.example.vn/lien-he"
    }
  ]
}
```

Gọi `PUT https://agent.example.vn/v1/shops/{shop_id}/faqs` với header `Authorization: Bearer <faq_write_key>` và `Content-Type: application/json`. Đây là **thay thế toàn bộ bộ FAQ**: câu cũ không có trong lần gửi mới sẽ bị xóa. Gửi lại cùng dữ liệu khi cần đồng bộ; phản hồi trả `{ "shop_id": "...", "count": 1, "revision": 2 }`. `source_url` nếu có phải cùng origin với `shop_url` đã đăng ký. Tối đa 500 FAQ mỗi lần; `id` phải duy nhất.

Có thể dùng script mẫu từ thư mục gốc repo:

```powershell
$env:ATI_SERVER_URL = 'http://localhost:8000'
$env:ATI_SHOP_ID = 'sample_store'
$env:ATI_FAQ_WRITE_KEY = '<khóa được cấp>'
.\.venv\Scripts\python.exe examples\push_faq.py examples\third_party_site\faqs.json
```

Script này chạy ở **backend hoặc tác vụ quản trị** của khách, không đặt trong JavaScript chạy trên trình duyệt. Sau khi gửi, backend có thể gọi `GET /v1/shops/{shop_id}/faqs` với cùng Bearer key để kiểm tra dữ liệu đã lưu. API trả 401 khi thiếu/sai khóa và 422 khi dữ liệu không hợp lệ; lỗi 422 không thay bộ FAQ hiện có.

## 3. Đồng bộ ưu đãi và sự kiện đã công bố

Backend khách gửi bản đầy đủ tới `PUT /v1/shops/{shop_id}/offers` bằng **cùng Bearer write key**. Ví dụ:

```json
{
  "offers": [
    {
      "id": "accessory-discount",
      "kind": "promotion",
      "title": "Ưu đãi phụ kiện",
      "description": "Giảm 10% phụ kiện khi nhập mã PHUKIEN10; áp dụng theo điều kiện hiển thị ở trang ưu đãi.",
      "starts_at": null,
      "ends_at": null,
      "source_url": "https://shop.example.vn/uu-dai"
    },
    {
      "id": "product-launch",
      "kind": "event",
      "title": "Ra mắt sản phẩm mới",
      "description": "Giới thiệu dòng sản phẩm mới tại cửa hàng.",
      "starts_at": "2027-01-10T09:00:00+07:00",
      "ends_at": "2027-01-10T18:00:00+07:00",
      "source_url": "https://shop.example.vn/su-kien/ra-mat"
    }
  ]
}
```

Ngày giờ trên chỉ là ví dụ; thay bằng thời gian thực trước khi đẩy. `event` bắt buộc có cả hai mốc thời gian ISO 8601 kèm múi giờ; `promotion` có thể bỏ cả hai nếu shop chưa công bố lịch. Nếu có một mốc thì phải có mốc còn lại, và `ends_at` phải sau `starts_at`. `source_url` phải cùng origin với `shop_url` đã đăng ký. Tối đa 100 mục, `id` duy nhất. Đây là bản **thay thế toàn bộ**: `{"offers":[]}` xóa bản công bố trước đó. `GET /v1/shops/{shop_id}/offers` với cùng khóa trả `revision`, `updated_at` và các mục đã lưu.

Có thể dùng script mẫu từ thư mục gốc repo:

```powershell
$env:ATI_SERVER_URL = 'https://agent.example.vn'
$env:ATI_SHOP_ID = 'sample_store'
$env:ATI_FAQ_WRITE_KEY = '<khóa được cấp>'
.\.venv\Scripts\python.exe examples\push_offers.py path\to\offers.json
```

Chỉ đồng bộ thông tin shop thực sự công bố. Chatbot đọc bản đã lưu khi khách hỏi sale hoặc sự kiện; nếu chưa đồng bộ hay thiếu lịch xác nhận, bot nói rõ chưa xác định và hướng khách liên hệ shop. Cập nhật hoặc xóa chương trình ở website thì backend cần đẩy bản mới để tránh thông tin cũ.

## 4. Nhúng widget

Khách thêm mã sau vào website, thay URL Agent Server và public key của shop:

```html
<script src="https://agent.example.vn/widget/loader.js"
        data-shop-key="pk_PUBLIC_KEY_DUOC_CAP"
        data-token-url="/api/chat-token" async></script>
```

`/api/chat-token` là endpoint **cùng origin trên website khách**: chỉ trả token khi khách đã đăng nhập, nếu chưa đăng nhập trả 401. Backend tạo JWT HS256 bằng `chat_token_secret` được cấp, với các claim `iss=<shop_id>`, `sub=<ID khách ổn định>`, `aud="ati-agent"`, `iat` và `exp` (Unix giây, thời hạn ngắn). Có thể xem cách ký trong `shop_demo/security.py`; khóa ký không đưa vào JavaScript. Khách đăng xuất cần gọi `ATI.reset()`. Widget khóa ô nhập và Agent Server trả 401 khi không có JWT hợp lệ, kể cả câu hỏi FAQ.

Khách truy cập website → widget lấy JWT từ endpoint cùng origin → gọi `/v1/chat` trên Agent Server → Agent Server tìm FAQ hoặc ưu đãi/sự kiện đã lưu của đúng shop. Lịch sử nằm ở Agent Server và chỉ đọc được bằng JWT của đúng khách và shop. Nếu cần tra đơn hàng/sản phẩm, website phải tích hợp thêm API dữ liệu đó với Agent Server; các thông tin này **vẫn là dữ liệu trực tiếp**, không nằm trong bản đồng bộ FAQ/ưu đãi. Bản demo hiện nối API sản phẩm, đơn hàng và ưu đãi của `shop_demo`. Trang tĩnh `examples/third_party_site` không có đăng nhập nên chỉ minh họa cách nhúng widget, chưa gửi câu hỏi được.

## Giới hạn bản thử

Khi có `AI_BOX_API_KEY`, LLM nhận diện ý định và chọn FAQ; câu trả lời vẫn lấy từ nội dung shop đã cung cấp. Lời chào được đáp ngắn gọn trong phạm vi hỗ trợ khách hàng; câu hỏi quá ngoài lề như chính trị bị từ chối. Khi thiếu FAQ/API/lịch ưu đãi hoặc không xác định được dữ kiện, bot hướng khách liên hệ trực tiếp shop. Chưa có embedding/RAG. Việc cấp shop dùng CLI, chưa có dashboard hay API tự đăng ký. SQLite phù hợp cho bản thử; khi triển khai nhiều khách và nhiều tiến trình cần chuyển sang kho dữ liệu vận hành phù hợp, bổ sung quota, chính sách lưu/xóa lịch sử, nhật ký đồng bộ và cơ chế xoay khóa.
