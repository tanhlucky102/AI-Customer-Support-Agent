# Hướng dẫn tích hợp cho shop

Tài liệu này trả lời câu hỏi: **một website muốn dùng chatbot thì phải cung cấp gì?**

Nguyên tắc: dữ liệu riêng của từng khách (đơn hàng, tài khoản) chỉ nằm trong hệ thống của shop, nên muốn tra
đơn thì shop phải cho Agent Server một đường đọc dữ liệu đó. Nhưng **không bắt shop viết API theo mẫu của
nhóm**: shop cung cấp được gì thì agent dùng cái đó. Mỗi thao tác shop khai báo trở thành một *khả năng*
(capability), và mô hình chỉ được thấy những tool ứng với khả năng có thật của shop
(`agent_server/tools.py: toolset_for`).

| Mức | Shop có gì | Shop phải làm | Agent được dùng | Ví dụ trong repo |
|-----|-----------|---------------|-----------------|------------------|
| **0 — Chỉ nội dung** | Không có API, có thể không có cả tài khoản khách | Đẩy FAQ/ưu đãi qua API ghi, hoặc gửi tài liệu Markdown; nhúng thẻ `<script>` | `search_knowledge_base`, `get_promotions`, `escalate_to_human` (khách vãng lai: không có tool chuyển nhân viên) | `sample_store` (cổng 8002, chế độ khách) |
| **1a — API theo hợp đồng C2** | Viết 3–4 endpoint theo mẫu | Cấu hình `{"profile": "standard", "base_url": ...}` + API key | + tra đơn, danh sách đơn, hồ sơ khách, tìm sản phẩm | `demo_shop` (cổng 8001) |
| **1b — API sẵn có của shop** | API riêng, định dạng bất kỳ | Viết **một file JSON ánh xạ**, không viết code | Các tool ứng với thao tác đã ánh xạ | `second_shop` (cổng 8003) |
| **2 — Nền tảng có sẵn** *(hướng phát triển)* | Shopify, WooCommerce, Haravan... | Chỉ nhập khóa | Như mức 1 | Chưa làm; mỗi nền tảng là một file ánh xạ dựng sẵn |

Shop có dữ liệu khách (mức 1, 2) phải có đăng nhập và một endpoint cấp token chat (khoảng 10 dòng nhờ
SDK, mục 4). Shop mức 0 không có tài khoản khách thì bật **chế độ khách** (mục 1): website tĩnh chỉ cần
nhúng thẻ `<script>`, không cần backend nào. Khi khách đăng nhập (token) thì token luôn được ưu tiên.

## 1. Mức 0: shop chỉ có nội dung

Đội vận hành cấp shop (mục 5), rồi backend của shop đẩy nội dung bằng khóa ghi `faq_write_key`:

```http
PUT /v1/shops/{shop_id}/faqs     Authorization: Bearer <faq_write_key>
{"faqs": [{"id": "giao-hang", "question": "Bao lâu nhận được hàng?",
           "answer": "Nội thành 48 giờ.", "keywords": ["giao hàng"],
           "source_url": "https://shop.example.vn/faq"}]}

PUT /v1/shops/{shop_id}/offers   (khuyến mãi và sự kiện có ngày bắt đầu/kết thúc)
```

- Mỗi lần đẩy sẽ **thay toàn bộ** bản cũ. FAQ được đánh chỉ mục ngay, nên tìm kiếm lai trên FAQ chạy giống
  như trên tài liệu.
- `source_url` phải cùng origin với website của shop, vì widget chỉ hiện link về đúng shop đó.
- Shop có nhiều tài liệu chính sách thì đặt file Markdown vào `data/kb/<shop_id>/` (định dạng ở mục 6) rồi
  chạy `python -m rag.ingest --shop <shop_id>`.
- **Chế độ khách** (`--guest-chat` khi cấp shop, mục 5): khách hỏi mà không cần đăng nhập. Widget tự tạo
  một mã ngẫu nhiên lưu trong trình duyệt và gửi trong header `X-ATI-Guest`; mã này chỉ dùng để gom lịch sử
  chat của chính khách đó, không phải đăng nhập. Server chỉ nhận khách khi shop đã bật cờ **và** không có API
  đơn hàng (`agent_server/store.py: guest_allowed`), nên khách không bao giờ chạm được tool đơn hàng. Khách
  cũng không có tool chuyển nhân viên (nhân viên không có cách liên hệ lại); agent đưa kênh liên hệ trong tài
  liệu. Giới hạn theo IP và hạn mức mỗi ngày của shop vẫn áp dụng. Test: `tests/test_guest_mode.py`.
- Khi khách hỏi đơn hàng, agent trả lời rằng shop chưa kết nối hệ thống đơn hàng, chỉ khách tới kênh liên hệ
  trong tài liệu và đề nghị chuyển cho nhân viên. Agent không được thấy tool tra đơn nên không thể bịa ra kết
  quả.

## 2. Mức 1a: API theo hợp đồng C2 (profile `standard`)

| Thao tác | Endpoint | Ghi chú |
|----------|----------|---------|
| `get_order` | `GET /api/v1/customers/{customer_id}/orders/{order_id}` | 404 khi đơn không thuộc khách |
| `list_orders` | `GET /api/v1/customers/{customer_id}/orders?limit=&status=` | |
| `get_customer` | `GET /api/v1/customers/{customer_id}` | |
| `search_products` *(tuỳ chọn)* | `GET /api/v1/products?q=&category=&max_price=&limit=` | |

Mọi request có header `Authorization: Bearer <API key>`. Các trường JSON xem trong
`shop_demo/api.py` và `agent_server/commerce.py` (`ORDER_FIELDS`, `CUSTOMER_FIELDS`, `PRODUCT_FIELDS`).

Ngày tháng của đơn: `eta` là ngày giao **dự kiến**, `delivered_at` là ngày giao **thật** (`YYYY-MM-DD`). Số ngày
(trễ bao nhiêu ngày, đã nhận hàng bao nhiêu ngày) do code tính (`agent_server/tools.py: date_facts`), không để model
tự trừ ngày. Shop không gửi `delivered_at` thì số ngày được tính từ `eta` và agent nói rõ đó là ước tính, ví dụ khi
khách hỏi còn trong hạn đổi trả không.
Thao tác nào không làm thì bỏ khỏi cấu hình, tool tương ứng sẽ không được bật.

## 3. Mức 1b: dùng API sẵn có của shop bằng file ánh xạ

Ví dụ thật: `examples/second_shop/commerce.json`. API của Phụ Kiện Xanh không viết cho framework này:

- đường dẫn `/v2/...`, xác thực bằng header `X-Api-Key`;
- dữ liệu bọc trong `data.order`, tên trường khác (`code`, `state`, `amount.value`, `lines[].title`);
- trạng thái riêng (`HANDED_TO_GHTK`...);
- không có API tìm sản phẩm.

```json
{
  "base_url": "http://localhost:8003",
  "auth": {"type": "header", "name": "X-Api-Key"},
  "operations": {
    "get_order": {
      "path": "/v2/orders/{order_id}", "params": {"customer": "{customer_id}"},
      "result": "data.order",
      "fields": {"id": "code", "status": "state", "total": "amount.value", "eta": "expectedBy",
                 "delivered_at": "deliveredAt", "tracking_code": "ghtkCode"},
      "items": {"path": "lines", "fields": {"name": "title", "qty": "quantity", "unit_price": "price"}}
    },
    "list_orders": {"path": "/v2/users/{customer_id}/orders",
                    "params": {"max": "{limit}", "state": "{status}"}, "result": "data.orders"}
  },
  "status_map": {"PLACED": "pending", "HANDED_TO_GHTK": "shipping", "DELIVERED": "delivered"},
  "links": {"order": "/account/orders/{order_id}"}
}
```

| Khoá | Ý nghĩa |
|------|---------|
| `profile` | `"standard"` để thừa hưởng toàn bộ mục 2; bỏ trống để tự khai báo |
| `auth.type` | `bearer` (mặc định) hoặc `header` cùng `name` |
| `operations.<op>.path` | Đường dẫn **tương đối**, bắt đầu bằng `/`, có biến `{customer_id}`, `{order_id}`; biến luôn được mã hoá URL |
| `operations.<op>.owner_field` | Đường dẫn chấm tới mã khách trong bản ghi (`buyer.id`). Bắt buộc khi API không lọc theo khách, ví dụ API quản trị trả đơn của mọi khách; bản ghi của khách khác bị coi như không tồn tại |
| `operations.<op>.params` | Query string; `"{limit}"`, `"{status}"`, `"{query}"`... lấy từ tham số tool, bỏ qua khi rỗng |
| `operations.<op>.result` | Đường dẫn chấm tới object/danh sách trong JSON trả về (`data.order`) |
| `operations.<op>.fields` | Tên trường chuẩn → đường dẫn trong dữ liệu của shop (`amount.value`) |
| `operations.<op>.items` | Danh sách sản phẩm trong đơn: `path` và `fields` |
| `status_map` | Trạng thái của shop → 6 trạng thái chuẩn; dùng cả hai chiều khi lọc |
| `links` | Mẫu link gắn dưới câu trả lời: `order` (trang một đơn), `product`, `orders` (trang danh sách đơn của khách, không có biến) |

`list_orders` dùng lại `fields`/`items` của `get_order` nếu không tự khai báo.

**An toàn:**

- `base_url` chỉ do đội vận hành nhập lúc cấp shop, shop không đổi được lúc chạy, nên không thể lợi dụng
  để server gọi tới địa chỉ lạ (SSRF).
- Chỉ dùng GET, không theo redirect, giới hạn 1 MB và có timeout.
- `customer_id` luôn lấy từ token đã xác thực, không bao giờ lấy từ mô hình.
- `get_order`, `list_orders`, `get_customer` phải **ràng buộc theo khách**: hoặc dùng `{customer_id}` trong
  `path`/`params` (shop tự lọc), hoặc khai báo `owner_field` (Agent Server kiểm tra từng bản ghi). Thao tác
  không ràng buộc bị `provision_shop` từ chối, và nếu lỡ được lưu thì cũng không bao giờ được cấp cho mô
  hình.
- Phản hồi 200 thiếu phần `result` đã khai báo bị coi là lỗi (agent xin lỗi và đề nghị chuyển nhân viên),
  không bị coi là "không có đơn".

Test của phần này nằm ở `tests/test_commerce.py` và `tests/test_review_fixes.py` (ràng buộc theo khách,
`owner_field`, đường dẫn tuyệt đối, phản hồi thiếu `result`).

## 4. Đăng nhập và token chat (shop có tài khoản khách)

Backend shop ký JWT HS256 cho khách đang đăng nhập (hợp đồng C3) bằng `chat_token_secret`:

```python
from ati_sdk import sign_user_token          # sdk/ati_sdk.py, không cần thư viện ngoài

@app.get("/api/chat-token")
def chat_token(user = Depends(current_user)):
    return {"token": sign_user_token(user.id, shop_id=SHOP_ID, secret=CHAT_TOKEN_SECRET)}
```

Nhúng widget vào trang:

```html
<script src="https://<agent-server>/widget/loader.js" data-shop-key="pk_..."
        data-token-url="/api/chat-token" data-login-url="/dang-nhap"></script>
```

- `data-token-url`: endpoint cấp token chat ở trên. Phải cùng origin với trang.
- `data-login-url` (không bắt buộc): trang đăng nhập của shop. Khi khách chưa đăng nhập, widget hiện link
  tới trang này. Link phải cùng origin với trang nhúng widget; link khác origin bị bỏ qua.
- Shop mức 0 dùng chế độ khách thì chỉ cần `data-shop-key`.
- **Đăng xuất:** trong hàm đăng xuất, backend shop gọi thêm một dòng
  `revoke_user_tokens(user.id, shop_id=..., write_key=..., server_url=...)` (SDK). Mọi token chat đã cấp
  cho khách đó mất hiệu lực ngay trên mọi thiết bị, kể cả token bị lộ (`POST /v1/shops/{shop_id}/logout`,
  khóa ghi của shop). Shop đăng xuất bằng JavaScript thì gọi `ATI.reset()`: widget tự báo Agent Server
  (`POST /v1/logout`). Token sống tối đa 1 giờ (SDK cấp 15 phút); token sống lâu hơn bị từ chối.

Widget không chứa gì riêng của shop nào: tên, lời chào, màu và câu gợi ý lấy từ cấu hình shop lúc cấp
(mục 5). Agent Server chỉ tự tạo shop Demo Mobile trên máy đã chạy `scripts/setup_demo.py` (hoặc khi
`DEMO_SHOP=1`); máy triển khai thật chỉ phục vụ các shop được cấp bằng `provision_shop`.

**Thử thêm một shop mới (đã chạy thật ngày 04/11/2026).** Một tiệm bánh không có API, không có tài khoản khách:

```bash
python -m agent_server.provision_shop --shop-id tiem_banh_mai --shop-url http://127.0.0.1:8002 \
    --title "Trợ lý Tiệm Bánh Mai" --color "#b45309" --guest-chat          # in ra public_key, faq_write_key
ATI_SHOP_ID=tiem_banh_mai ATI_FAQ_WRITE_KEY=sk_... python examples/push_faq.py faqs.json
# mở http://127.0.0.1:8002/?shop_key=pk_... và hỏi: widget mang tên, màu của tiệm, trả lời từ FAQ có trích dẫn
```

## 5. Cấp shop (đội vận hành)

```bash
# Mức 0
python -m agent_server.provision_shop --shop-id sample_store --shop-url http://localhost:8002 --title "Trợ lý Sample Store" --guest-chat
# Mức 1
python -m agent_server.provision_shop --shop-id second_shop --shop-url http://localhost:8003 \
    --title "Trợ lý Phụ Kiện Xanh" --commerce-config examples/second_shop/commerce.json \
    --commerce-api-key <key do shop cấp>
python -m agent_server.rotate_chat_secret --shop-id second_shop    # xoay khóa ký token
```

Lệnh in `public_key`, `faq_write_key`, `chat_token_secret` **một lần**, cùng mức tích hợp và danh sách khả
năng. Khóa ghi chỉ được lưu dưới dạng SHA-256. Phiếu chuyển nhân viên của shop đọc tại
`GET /v1/shops/{shop_id}/tickets` (cùng khóa ghi).

## 6. Định dạng tài liệu Markdown

```markdown
---
title: Chính sách đổi trả
category: returns          # returns, warranty, shipping, payment, orders, account,
updated_at: 2026-10-01     # membership, promotions, product_guide, store
---
# Chính sách đổi trả
Đoạn giới thiệu.

## Thời hạn đổi trả
Nội dung một mục (đoạn văn, danh sách, bảng). Mỗi mục nên tự đủ nghĩa khi đọc riêng.
```

Mỗi mục `##` là một đoạn cha mà agent đọc và trích dẫn. Link trích dẫn trỏ tới
`<shop_url>/help/<slug>#muc-<n>`.

## 7. Vận hành: nhiều tiến trình và model dự phòng

- **Nhiều tiến trình.** Mọi trạng thái dùng chung giữa các lượt chat nằm trong PostgreSQL: một hội thoại chỉ chạy
  một lượt tại một thời điểm (cột `conversations.busy_until`, tự hết hạn nếu tiến trình chết), giới hạn tần suất và
  hạn mức ngày (bảng `rate_events`), mỗi shop một lần đẩy FAQ (`pg_advisory_lock`). Vì vậy có thể chạy
  `uvicorn agent_server.app:app --workers 4` hoặc nhiều container sau load balancer. Trong RAM của từng tiến trình chỉ
  còn cache dòng shop 10 giây. `RATE_LIMIT_BACKEND=memory` đếm trong RAM, chỉ dùng khi chạy một tiến trình.
  Test: `tests/test_scale_out.py`.
- **Model dự phòng.** AI Box lỗi hoặc treo trước khi khách nhận được chữ nào (sau một lần thử lại) thì lượt đó chuyển
  sang model thứ hai, nếu được cấu hình; chỉ khi cả hai lỗi agent mới trả lời theo quy tắc. Model thứ hai là bất kỳ
  model nào LiteLLM hỗ trợ, ví dụ model chạy trên máy qua Ollama:

  ```
  LLM_FALLBACK_MODEL=ollama_chat/qwen2.5:7b
  LLM_FALLBACK_BASE_URL=http://localhost:11434
  LLM_FALLBACK_API_KEY=            # để trống với Ollama
  ```

  Sự kiện `done` và bảng `traces` ghi model nào đã trả lời lượt đó.
