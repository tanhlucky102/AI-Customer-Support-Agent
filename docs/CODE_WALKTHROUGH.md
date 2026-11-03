# Hướng dẫn đọc code: AI Customer Support Agent

Tài liệu này giúp từng thành viên tự giải thích phần code của mình khi thầy hỏi. Đọc theo thứ tự:
mục 1 (bản đồ), mục 2 (một câu hỏi đi qua code), rồi phần của mình ở mục 3, và tự trả lời thử mục 4.

Quy ước: `file:dòng` là đường dẫn tương đối từ thư mục gốc repo. Số dòng ứng với code lúc viết tài liệu
(04/11/2026): nhánh `final` cộng các thay đổi chưa commit, gồm cả chế độ khách vãng lai. Nếu sửa code, số
dòng có thể lệch vài dòng; tìm theo tên hàm là chắc nhất.

## 1. Bản đồ thư mục

| Thư mục / file | Làm gì | Phụ trách |
|---|---|---|
| `agent_server/agent.py` | Vòng lặp agent Reason → Act → Observe (hợp đồng C5) | M1 |
| `agent_server/tools.py` | 7 tool: schema cho model, kiểm tra lại tham số bằng Pydantic, chạy tool | M1 |
| `agent_server/prompts.py` | System prompt, quy tắc tool sinh theo khả năng của shop | M1 |
| `agent_server/app.py`, `auth.py`, `rate_limit.py` | FastAPI: phục vụ widget, `POST /v1/chat` (SSE), API ghi FAQ/ưu đãi, xác thực JWT, giới hạn tần suất | M1 |
| `agent_server/commerce.py` | CommerceAdapter: đọc API đơn hàng của shop qua file ánh xạ JSON, ràng buộc theo khách | M1 |
| `agent_server/store.py` | Bảng shop, hội thoại, phiếu chuyển nhân viên, trace (PostgreSQL) | M1 |
| `agent_server/fallback.py` | Trả lời theo quy tắc khi AI Box lỗi | M1 |
| `agent_server/provision_shop.py`, `rotate_chat_secret.py`, `cli.py` | Lệnh cho đội vận hành: cấp shop, xoay khóa, chat trong terminal | M1 |
| `shared/` (`llm.py`, `db.py`, `text.py`, `settings.py`, `pii.py`) | Gọi LLM qua LiteLLM, kết nối PostgreSQL + pgvector, xử lý tiếng Việt, đọc `.env`, che PII | M1 |
| `sdk/ati_sdk.py` | `sign_user_token`: backend shop ký JWT cho khách đã đăng nhập | M1 |
| `rag/` | Đọc Markdown, chia chunk, Contextual Retrieval (tắt mặc định), embedding, BM25, tìm kiếm lai, nạp kho | M2 |
| `data/kb/` | Kho tri thức Markdown của 2 shop | M2 nạp vào index; M3 hiển thị ở trang `/help` |
| `shop_demo/` | Website Demo Mobile (FastAPI + SQLite): đăng nhập, đơn hàng, API C2, cấp token chat | M3 |
| `widget/` | `loader.js` (chạy trên trang shop) và giao diện chat trong iframe (`frame.*`) | M3 |
| `examples/` | Shop thứ hai có API riêng + `commerce.json`; trang tĩnh Sample Store (mức 0, chế độ khách); script đẩy FAQ/ưu đãi | M3 |
| `eval/` | Bộ dữ liệu và harness đánh giá (truy hồi, chọn tool, groundedness, từ chối, tấn công); `human_review.py` để cả nhóm chấm lại judge; kết quả ở `eval/results/` | M3 |
| `scripts/` | `setup_demo`, `run_all`, `smoke_live`, `bad_cases`, `trace_stats`, `browser_smoke.mjs`, `record_demo.mjs` | M3 |
| `tests/` | Unit test chạy với embedder giả, không gọi LLM | Chung: `test_agent_server`, `test_commerce`, `test_resilience`, `test_guest_mode` (M1), `test_rag` (M2), `test_review_fixes` (cả nhóm) |
| `docs/`, `README.md`, `Dockerfile`, `docker-compose.yml` | Hợp đồng C1–C6, hướng dẫn tích hợp, chạy bằng Docker | Chung |

### Ranh giới giữa các phần (trả lời câu "kiến trúc có sạch không")

- Agent core không biết HTTP hay widget (`agent_server/agent.py:4`). Cùng
  một hàm `agent.run` được gọi từ API chat (`agent_server/app.py:401`), từ
  terminal (`agent_server/cli.py:31`) và từ bộ eval
  (`eval/agent_eval.py:106`).
- Chỉ `commerce.py` nói chuyện với API của shop; chỉ `rag/search.py` biết cách tìm tài liệu. Agent chỉ
  thấy hai thứ đó qua `TurnContext.adapter` và `TurnContext.search` (`agent_server/tools.py:70`).
- Mỗi ranh giới có hợp đồng viết ra giấy: `docs/contracts.md` (C1 tìm kiếm, C2 API shop, C3 JWT, C4 SSE,
  C5 agent, C6 bộ eval).

## 2. Một câu hỏi đi qua code như thế nào

### 2.1 Trước khi hỏi: widget lấy token đăng nhập

| Bước | Ở đâu | Chuyện gì xảy ra |
|---|---|---|
| 1 | `shop_demo/templates/base.html:84` | Trang shop nhúng `loader.js` với `data-shop-key`, `data-token-url` và (không bắt buộc) `data-login-url` |
| 2 | `widget/loader.js:51` | Tạo Shadow DOM và iframe `/widget/frame?shop_key=...&parent_origin=...` (`widget/loader.js:68`); thêm `login_url` nếu trang đăng nhập cùng origin với shop (`widget/loader.js:72`) |
| 3 | `agent_server/app.py:218` | Agent Server chỉ trả iframe nếu `parent_origin` nằm trong `allowed_origins` của shop, và đặt CSP `frame-ancestors` (`agent_server/app.py:225`) |
| 4 | `widget/frame.js:447` | iframe tải `/v1/widget-config`, báo `ati:ready` cho trang cha |
| 5 | `widget/loader.js:21` | `loader.js` gọi `/api/chat-token` của shop (bắt buộc cùng origin, `widget/loader.js:29`) |
| 6 | `shop_demo/app.py:632` → `shop_demo/security.py:30` → `sdk/ati_sdk.py:25` | Shop ký JWT HS256 (`iss`=shop, `sub`=mã khách, `aud`, `exp` sau 900 s) |
| 7 | `widget/frame.js:395` | iframe nhận `ati:auth` (chỉ từ đúng trang cha, `widget/frame.js:396`) và giữ token trong bộ nhớ; chưa đăng nhập thì hiện link tới `login_url` của shop (`widget/frame.js:162`) |

### 2.2 "Đơn SO10293 của tôi tới đâu rồi?"

1. **Widget gửi câu hỏi.** `send` (`widget/frame.js:365`) vẽ bong bóng của khách và một
   bong bóng trống cho bot, rồi `callChat` (`widget/frame.js:339`) gửi
   `POST /v1/chat` với `Authorization: Bearer <JWT>` và body `{shop_key, message, conversation_id}`. Gặp 401
   thì xin token mới và thử lại một lần (`widget/frame.js:347`); gặp 429 thì báo khách chờ.
2. **Agent Server kiểm tra.** `chat` (`agent_server/app.py:366`) lần lượt:
   - `require_widget_origin` (`agent_server/app.py:185`): trình duyệt luôn gửi `Origin` với POST,
     và nó phải là origin của Agent Server, tức là chỉ iframe widget được gọi.
   - `require_shop_key` (`agent_server/app.py:153`): tìm shop theo public key.
   - `require_customer` (`agent_server/app.py:169`) gọi `verify_customer_token`
     (`agent_server/auth.py:12`): kiểm tra `alg`, chữ ký, `iss` = shop, `aud`,
     `exp`, `iat`. Mã khách lấy từ `sub` của token. Body không được có `customer_id`
     (`agent_server/app.py:104`).
   - Hội thoại cũ phải thuộc đúng khách (`agent_server/app.py:377`).
   - Giới hạn tần suất (`agent_server/app.py:380` → `agent_server/rate_limit.py:27`).
3. **Mở luồng SSE.** Hàm sinh `events` (`agent_server/app.py:386`) giữ khóa của hội thoại
   (`agent_server/app.py:392`), đọc 8 tin gần nhất hoặc tạo hội
   thoại mới, rồi dựng `TurnContext` (`agent_server/app.py:359`) với adapter của shop
   (`agent_server/commerce.py:296`) và hàm `search`.
4. **Agent chuẩn bị.** `run` (`agent_server/agent.py:39`) chọn bộ tool theo khả năng của shop
   (`agent_server/tools.py:272`), dựng system prompt (`agent_server/prompts.py:85`),
   tin nhắn ngày giờ (`agent_server/prompts.py:93`), lịch sử và câu hỏi, rồi phát `status`.
5. **Lượt gọi model thứ nhất.** Vòng lặp (`agent_server/agent.py:56`) gọi
   `LLMProvider.stream` (`shared/llm.py:92`). LiteLLM gửi tới DeepSeek qua AI Box; chữ được
   đẩy ra từng mẩu, còn lời gọi tool được ghép theo `index` (`shared/llm.py:122`).
   Model chọn `get_order_status` với `{"order_id": "SO10293"}`; theo luật prompt, nó không viết chữ nào trước khi gọi tool.
6. **Chạy tool.** Agent ghi lời gọi tool vào `messages` (`agent_server/agent.py:75`),
   phát `status` "Đang kiểm tra đơn hàng...", rồi gọi `execute` (`agent_server/tools.py:285`):
   parse JSON, bỏ giá trị `null`, kiểm tra bằng `OrderArgs` (`agent_server/tools.py:39`,
   regex cho mã đơn, cấm trường lạ).
7. **Gọi API của shop với mã khách từ token.** `get_order_status` (`agent_server/tools.py:148`)
   gọi `ctx.adapter.get_order(args.order_id, ctx.customer_id)`. `RestCommerceAdapter.get_order`
   (`agent_server/commerce.py:243`) dùng `_get` (`agent_server/commerce.py:146`):
   mã hoá URL các biến, dựng path `/api/v1/customers/{customer_id}/orders/{order_id}`
   (`agent_server/commerce.py:40`), gửi GET kèm API key, không
   theo redirect, có timeout. 404 thành `None`; mã khác 200 thành `CommerceError`. Sau đó `_owned`
   (`agent_server/commerce.py:197`) kiểm tra `owner_field` nếu có, và `_order` (`agent_server/commerce.py:226`)
   đổi tên trường, đổi trạng thái về 6 giá trị chuẩn, gắn link trang đơn.
8. **Phía shop.** `get_order` của shop demo (`shop_demo/api.py:62`) chỉ tìm
   `WHERE id = ? AND customer_id = ?`, nên đơn của khách khác trả 404 giống hệt đơn không tồn tại.
9. **Kết quả về model.** Tool thêm link "Xem đơn SO10293" vào `ctx.links` (`agent_server/tools.py:153`)
   và chỉ đưa cho model các trường cần (`agent_server/tools.py:137`). Agent thêm một tin
   `role: tool` chứa JSON (`agent_server/agent.py:88`) rồi quay lại vòng lặp.
10. **Lượt gọi model thứ hai.** Model viết câu trả lời (trạng thái, ngày giao dự kiến, mã vận đơn). Mỗi mẩu
    chữ là một sự kiện `token`, được `app.py` đổi thành SSE (`agent_server/app.py:192`, `agent_server/app.py:412`).
11. **Kết thúc lượt.** `cited_sources` (`agent_server/agent.py:33`) gom nguồn; dữ liệu đơn không
    cần số `[n]` nên chỉ có link trang đơn. Agent phát `citations` rồi `done` (kèm `reply`, `tools`, `usage`).
12. **Lưu và trace.** `app.py` lưu câu hỏi và câu trả lời (`agent_server/store.py:232`), ghi trace
    chỉ gồm tên tool, tham số đã che và thời gian (`agent_server/app.py:349`,
    `agent_server/store.py:293`), rồi gửi SSE `done` (`agent_server/app.py:442`). Có lỗi bất kỳ thì gửi
    `error` (`agent_server/app.py:433`), không bao giờ cắt luồng im lặng.
13. **Widget vẽ.** `consumeStream` (`widget/frame.js:302`) tách từng khối SSE
    (`widget/frame.js:290`): `status` hiện dòng trạng thái, `token` nối chữ và vẽ lại bằng
    `renderMarkdown` (`widget/frame.js:65`), `replace` thay toàn bộ bong bóng, `citations` gọi
    `addSources` (`widget/frame.js:238`, chỉ hiện link cùng origin với shop), `done` lưu `conversation_id`.

`scripts/smoke_live.py` kiểm tra đúng luồng này trên server thật: câu trả lời phải có mã vận đơn mà chỉ API
shop biết (`scripts/smoke_live.py:53`), và khách khác hỏi cùng mã đơn thì không thấy tổng tiền
(`scripts/smoke_live.py:58`).

### 2.3 Câu hỏi chính sách: "Phí giao hàng cho đơn 300 nghìn là bao nhiêu?"

1. Model chọn `search_knowledge_base` với một truy vấn tiếng Việt đầy đủ (mô tả tool yêu cầu như vậy,
   `agent_server/tools.py:228`).
2. `search_knowledge_base` (`agent_server/tools.py:122`) chạy `ctx.search` trong thread
   riêng, vì model embedding và reranker chạy đồng bộ.
3. `search` (`rag/search.py:49`) gọi `_search` (`rag/search.py:64`):
   - Nhánh vector: BGE-M3 mã hoá câu hỏi, lấy 20 chunk gần nhất theo cosine trên chỉ mục HNSW
     (`rag/search.py:69` → `rag/store.py:87`).
   - Nhánh từ khóa: `query_terms` (`shared/text.py:45`) bỏ dấu, bỏ hư từ, thêm cặp âm tiết
     (ví dụ `giao_hang`); BM25 trong RAM lấy 20 chunk (`rag/search.py:78`).
   - Gộp hai danh sách bằng RRF với k = 60 (`rag/search.py:37`).
   - Đổi chunk thành mục cha `##`, mỗi mục lấy điểm của chunk tốt nhất (`rag/search.py:84`),
     gắn link `/help/<slug>#muc-<n>` (`rag/search.py:99`).
   - Reranker `bge-reranker-v2-m3` chấm lại từng cặp (câu hỏi, mục) (`rag/search.py:111`),
     sắp xếp, giữ 5 mục.
4. Nếu điểm cao nhất nhỏ hơn τ = 0.1 thì trả `[]` (`rag/search.py:59`). Khi đó tool
   trả `no_results` kèm lời dặn "tìm lại bằng từ khác một lần, nếu vẫn không có thì đừng đoán"
   (`agent_server/tools.py:125`).
5. Có kết quả thì mỗi mục được đánh số bằng `ctx.cite` (`agent_server/tools.py:80`). Model viết
   "... 30.000₫ [1]" theo luật trích dẫn (`agent_server/prompts.py:18`).
6. `cited_sources` chỉ giữ những số vừa xuất hiện trong câu trả lời vừa thật sự được tool cấp
   (`agent_server/agent.py:34`). Widget hiện "[1] Tên tài liệu – Mục", link mở đúng mục trên trang
   trợ giúp do shop render với cùng anchor (`shop_demo/help_pages.py:41`).

### 2.4 Khách vãng lai ở shop mức 0 (Sample Store, cổng 8002)

Shop không có tài khoản khách thì không ký được JWT. Luồng khác ở bốn chỗ:

1. Trang tĩnh nhúng `loader.js` với `pk_sample_store` và không có `data-token-url`
   (`examples/third_party_site/index.html:25`), nên `loader.js` gửi `ati:auth` với token rỗng.
2. `/v1/widget-config` trả `guest_chat` (`agent_server/app.py:255`). Khi đó widget lấy mã khách
   (`widget/frame.js:136`): 32 ký tự hex ngẫu nhiên từ `crypto.getRandomValues`, lưu trong `localStorage`
   theo từng shop key. `authHeaders` (`widget/frame.js:127`) gửi `X-ATI-Guest` khi không có token.
3. `require_customer` (`agent_server/app.py:169`): có Bearer thì luôn dùng token; không có thì chỉ nhận
   mã khách nếu đúng định dạng và `guest_allowed(shop)` (`agent_server/store.py:137`): shop bật cờ
   `guest_chat` **và** không có `commerce_config`. Mã khách thành `customer_id = "guest:<mã>"`.
4. `TurnContext.guest` (`agent_server/tools.py:86`) báo cho agent. `toolset_for` bỏ tool đơn hàng và
   `escalate_to_human` (`agent_server/tools.py:277`); prompt nói đây là khách chưa đăng
   nhập và dặn đưa kênh liên hệ trong tài liệu thay vì chuyển nhân viên (`agent_server/prompts.py:79`).

Phần còn lại (RAG, SSE, lưu lịch sử theo `customer_id`) giống hệt mục 2.2 và 2.3. Test:
`tests/test_guest_mode.py:53`, `tests/test_guest_mode.py:73`.

## 3. Phần của từng thành viên

### 3.1 M1 – Nguyễn Tiến Anh: agent core, Agent Server, bảo mật

**`agent_server/agent.py`**

| Hàm | Dòng | Làm gì |
|---|---|---|
| `run` | `agent_server/agent.py:39` | Vòng lặp tối đa `MAX_ITERATIONS` = 5 lần gọi model (`agent_server/agent.py:23`); lần cuối ép `tool_choice="none"` để model phải trả lời |
| `history_messages` | `agent_server/agent.py:27` | Lấy 8 tin gần nhất, xoá số `[n]` cũ để không trùng số của lượt mới |
| `cited_sources` | `agent_server/agent.py:33` | Giao của "số có trong câu trả lời" và "số tool đã cấp", cộng link đơn/sản phẩm |
| `valid_json` | `agent_server/agent.py:115` | Gửi lại tham số tool cho gateway; JSON hỏng thì thay bằng `{}` |
| `_fallback` | `agent_server/agent.py:124` | Gọi `fallback.answer`, phát lại như một lượt bình thường với `mode="fallback"` |

Tại sao làm thế này:
- **Có trần số vòng lặp**: không lặp vô hạn, chi phí mỗi câu có giới hạn; lần cuối không cho gọi tool.
- **Sự kiện `replace`**: prompt bắt gọi tool im lặng (`agent_server/prompts.py:15`), nhưng đôi khi
  model vẫn viết "Để mình kiểm tra..." rồi mới gọi tool. Chữ đó đã được stream tới khách, nên phải rút lại:
  `replace` mang câu trả lời tính tới trước lượt này (`agent_server/agent.py:71`).
- **Chuyển nhân viên kết thúc lượt bằng câu cố định** (`agent_server/agent.py:91`): model
  không thể tự hứa thời gian khác.
- **AI Box lỗi**: chưa có chữ nào thì chuyển sang fallback; đã có chữ thì báo `error`, không trộn hai kiểu trả
  lời trong một bong bóng (`agent_server/agent.py:95`).

**`agent_server/tools.py`**

| Hàm / lớp | Dòng | Làm gì |
|---|---|---|
| `Args` và các lớp con | `agent_server/tools.py:29` | Tham số tool, `extra="forbid"`: trường lạ như `customer_id` bị từ chối |
| `TurnContext` | `agent_server/tools.py:70` | Trạng thái một lượt: shop, mã khách (từ token), adapter, hàm search, nguồn trích dẫn, link |
| `TOOLS` | `agent_server/tools.py:227` | 7 tool: tên, mô tả cho model, lớp tham số, câu trạng thái, khả năng cần có, hàm chạy |
| `toolset_for` | `agent_server/tools.py:272` | Chỉ đưa tool có khả năng thật; shop mức 0 và khách vãng lai không có tool đơn hàng |
| `execute` | `agent_server/tools.py:285` | Kiểm tra tham số và chạy; mọi lỗi thành dữ liệu cho model giải thích |
| `escalate_to_human` | `agent_server/tools.py:217` | Tạo phiếu (một phiếu cho mỗi lượt nhờ `turn_key`), trả câu cố định |

Tại sao làm thế này:
- **Model không bao giờ nhập mã khách** (`agent_server/tools.py:3`). Không tool nào có tham
  số `customer_id`; nếu model bị lừa và tự thêm, Pydantic từ chối trước khi gọi shop (test:
  `tests/test_agent_server.py:94`).
- **Lỗi thành dữ liệu** (`shop_system_unavailable`, `tool_failed`, `agent_server/tools.py:301`): model xin
  lỗi và đề nghị chuyển nhân viên thay vì đoán, và lượt chat không bị chết.
- **Không có tool ghi, trừ `escalate_to_human`**: agent chỉ đọc. Nếu bị prompt injection, tệ nhất là trả lời
  sai chữ, không thể huỷ đơn hay hoàn tiền. Việc cần người làm thì đi qua phiếu cho nhân viên.
- **Bỏ tham số `category`** khỏi tool tìm kiếm (`agent_server/tools.py:34`): eval v2 cho thấy model đoán
  sai danh mục làm mất tài liệu đúng.
- **Che PII trước khi đưa cho model** (`agent_server/tools.py:169`): email, số điện thoại, địa chỉ.

**`agent_server/prompts.py`**

| Hàm | Dòng | Làm gì |
|---|---|---|
| `BASE` | `agent_server/prompts.py:10` | Luật trả lời, luật trích dẫn, luật an toàn |
| `tool_rules` | `agent_server/prompts.py:50` | Sinh luật dùng tool theo bộ tool thật của shop |
| `system_prompt` | `agent_server/prompts.py:85` | Ghép tên shop, giọng điệu, luật tool |
| `context_message` | `agent_server/prompts.py:93` | Ngày giờ Việt Nam; nếu khách viết tiếng Anh thì dặn trả lời tiếng Anh |

Tại sao làm thế này:
- **Phần đầu prompt không đổi giữa các lượt** (`agent_server/prompts.py:2`) để AI Box cache được;
  ngày giờ nằm ở tin nhắn thứ hai.
- **Kết quả tool là DỮ LIỆU, không phải lệnh** (`agent_server/prompts.py:28`): tài liệu và FAQ
  do shop đẩy lên, dữ liệu đơn có chữ do khách gõ, nên đều có thể chứa câu giả lệnh. Prompt chỉ là lớp mềm;
  lớp cứng nằm trong code (bộ tool, ràng buộc theo khách, che PII).
- **Phát hiện tiếng Anh bằng code** (`agent_server/prompts.py:96`): chỉ ghi luật trong prompt thì thua kết quả
  tool tiếng Việt (eval v8).

**`agent_server/app.py` (cùng `auth.py`, `rate_limit.py`)**

| Hàm / lớp | Dòng | Làm gì |
|---|---|---|
| `chat` / `events` | `agent_server/app.py:366` | Xác thực, giới hạn, chạy agent, đổi sự kiện thành SSE, lưu, trace |
| `ConversationLocks` | `agent_server/app.py:74` | Một khóa cho mỗi hội thoại: tin thứ hai chờ tin thứ nhất lưu xong |
| `require_customer` | `agent_server/app.py:169` | Mã khách từ JWT; shop mức 0 bật `guest_chat` thì nhận khách vãng lai |
| `frame` | `agent_server/app.py:218` | Trả iframe kèm CSP `frame-ancestors` |
| `sync_faqs`, `sync_offers` | `agent_server/app.py:271` | Shop đẩy FAQ/ưu đãi bằng khóa ghi; kiểm tra trùng id, origin của link |
| `ChatLimiter.check` | `agent_server/rate_limit.py:27` | Cửa sổ 1 phút theo khách và theo IP, hạn mức theo ngày của shop |

Tại sao làm thế này:
- **SSE thay vì WebSocket**: mỗi lượt chỉ cần luồng một chiều server → widget, chạy trên HTTP thường, không
  giữ kết nối giữa các lượt; header `X-Accel-Buffering: no` (`agent_server/app.py:445`) để proxy không gom chữ lại.
  Widget đọc bằng `fetch` + `ReadableStream` vì `EventSource` không gửi được POST và header `Authorization`.
- **Luôn kết thúc bằng `done` hoặc `error`** (`agent_server/app.py:424`); không log câu hỏi vì có thể chứa
  dữ liệu cá nhân (`agent_server/app.py:425`).
- **Giới hạn thời gian một lượt** (`agent_server/app.py:43`): gateway chậm không giữ khóa hội thoại mãi.
- **Iframe từ origin của Agent Server**: JS của trang shop không đọc được hội thoại; CSP chỉ cho các origin
  đã đăng ký nhúng.
- **Chế độ khách chỉ cho shop mức 0**: mã khách do trình duyệt tự sinh nên không chứng minh được là ai. Vì vậy
  nó chỉ dùng để gom lịch sử chat, và chỉ được nhận ở shop không có dữ liệu riêng của khách. Khách không có
  tool chuyển nhân viên vì nhân viên không có cách liên hệ lại người không có tài khoản.

**`agent_server/commerce.py`**

| Hàm / lớp | Dòng | Làm gì |
|---|---|---|
| `STANDARD_PROFILE` | `agent_server/commerce.py:36` | Ánh xạ của hợp đồng C2: shop làm đúng mẫu thì không cần khai báo gì |
| `resolve_config` | `agent_server/commerce.py:88` | Trộn cấu hình của shop lên profile |
| `config_problems`, `_scoped` | `agent_server/commerce.py:111` | Path phải tương đối; thao tác của một khách phải có `{customer_id}` hoặc `owner_field` |
| `RestCommerceAdapter._get` | `agent_server/commerce.py:146` | Dựng URL, gọi GET, xử lý 404/lỗi/kích thước, lấy phần `result` |
| `_owned`, `_order` | `agent_server/commerce.py:197` | Kiểm tra chủ đơn; đổi bản ghi của shop về trường và trạng thái chuẩn |
| `list_orders` | `agent_server/commerce.py:251` | Lọc trạng thái ở shop nếu ánh xạ 1-1, không thì lấy 50 đơn và lọc tại chỗ |
| `orders_page` | `agent_server/commerce.py:213` | Link trang "Đơn hàng của tôi" lấy từ `links.orders` trong ánh xạ, không viết cứng theo shop |

Tại sao làm thế này:
- **Ràng buộc theo khách nằm ở adapter, không ở prompt**: prompt có thể bị vượt qua, code thì không. Thao tác
  không ràng buộc bị `provision_shop` từ chối, và nếu lỡ được lưu thì cũng không thành khả năng
  (`agent_server/commerce.py:136`).
- **Đơn của khách khác giống hệt đơn không tồn tại** (`agent_server/commerce.py:248`): không lộ việc mã đơn có thật.
- **Chống SSRF**: `base_url` chỉ do đội vận hành nhập; path tương đối; chỉ GET; `follow_redirects=False`
  (`agent_server/commerce.py:165`); có timeout; đọc phản hồi theo từng mảnh và dừng khi quá 1 MB
  (`agent_server/commerce.py:172`).
- **Phản hồi 200 thiếu dữ liệu là lỗi** (`agent_server/commerce.py:183`), không bị hiểu thành "không có đơn".
- **File JSON thay vì code**: shop có API riêng chỉ viết ánh xạ (`examples/second_shop/commerce.json`).

**`agent_server/store.py`, `fallback.py`, `provision_shop.py`**

| Hàm | Dòng | Làm gì |
|---|---|---|
| `verify_faq_key` | `agent_server/store.py:93` | So SHA-256 của khóa ghi bằng `compare_digest`; DB chỉ lưu bản băm |
| `replace_faqs` | `agent_server/store.py:159` | Thay cả bộ FAQ trong một giao dịch, khóa dòng shop (`FOR UPDATE`) |
| `create_ticket` | `agent_server/store.py:271` | `ON CONFLICT (idempotency_key)`: gọi hai lần trong một lượt vẫn một phiếu |
| `guest_allowed` | `agent_server/store.py:137` | Khách vãng lai chỉ được phép ở shop bật `guest_chat` và không có API đơn hàng |
| `fallback.answer` | `agent_server/fallback.py:37` | Có mã đơn thì tra đơn; hỏi "đơn của tôi" thì liệt kê; còn lại trích nguyên văn mục tài liệu tốt nhất |
| `load_commerce_config` | `agent_server/provision_shop.py:21` | Kiểm tra `base_url`, tên thao tác, `config_problems` trước khi cấp shop |

Tại sao có fallback: AI Box là dịch vụ ngoài. Fallback không sinh chữ (`agent_server/fallback.py:3`), chỉ
trích dữ liệu đơn hoặc nguyên văn tài liệu, kèm ghi chú "đây là câu trả lời tự động", nên không thể bịa.

**`shared/` và `sdk/`**

| Hàm / lớp | Dòng | Làm gì |
|---|---|---|
| `LLMProvider.stream` | `shared/llm.py:92` | Gọi LiteLLM có stream; thử lại 1 lần nếu lỗi tạm thời và chưa có chữ nào (`shared/llm.py:138`) |
| `transient` | `shared/llm.py:165` | Timeout, mất kết nối, 429, 5xx thì đáng thử lại; 400, sai key thì không |
| `pool`, `connect` | `shared/db.py:235` | Pool kết nối PostgreSQL, mỗi `with connect()` là một giao dịch |
| `_start_embedded` | `shared/db.py:193` | Không có `DATABASE_URL` thì chạy PostgreSQL nhúng (pgserver), xử lý đường dẫn có dấu |
| `fold`, `query_terms` | `shared/text.py:24` | Bỏ dấu tiếng Việt; tách âm tiết, bỏ hư từ, thêm bigram |
| `mask_*`, `PiiFilter` | `shared/pii.py:36` | Che email, số điện thoại trong log và trace |
| `sign_user_token` | `sdk/ati_sdk.py:25` | Ký JWT HS256, không cần thư viện ngoài, mặc định sống 900 s |

Tại sao: LiteLLM để đổi nhà cung cấp chỉ bằng cấu hình (`shared/llm.py:1`); `num_retries` của
LiteLLM đặt 0 để nhóm tự kiểm soát việc thử lại (sau khi khách đã thấy chữ thì không gọi lại được). Một
PostgreSQL chứa cả dữ liệu chat lẫn vector (pgvector) nên không cần thêm vector database.

### 3.2 M2 – Nguyễn Đức Anh: RAG

| File | Hàm / lớp | Dòng | Làm gì |
|---|---|---|---|
| `documents.py` | `parse` | `rag/documents.py:42` | Đọc front matter, tiêu đề `#`, các mục `##`; đoạn mở đầu nhập vào mục đầu tiên |
| | `faq_document` | `rag/documents.py:88` | FAQ được shop đẩy lên thành tài liệu một mục, tìm như tài liệu thường |
| `chunking.py` | `split_section` | `rag/chunking.py:51` | Cắt mục theo câu/dòng, tối đa 500 token, chồng khoảng 80 token |
| | `chunk_document` | `rag/chunking.py:76` | Trả (mục, id mục, các chunk con) cho mỗi mục |
| `contextual.py` | `contexts_for` | `rag/contextual.py:46` | LLM viết 1–2 câu ngữ cảnh cho mỗi chunk, cache theo hash |
| `ingest.py` | `ingest_documents` | `rag/ingest.py:32` | Bỏ qua tài liệu không đổi, embed, ghi DB, xoá tài liệu đã bị xoá |
| `models.py` | `BgeM3Embedder`, `Reranker` | `rag/models.py:43` | Nạp model một lần cho cả tiến trình, fp16 khi có GPU |
| `store.py` | `replace_document` | `rag/store.py:41` | Thay một tài liệu (mục, chunk, vector, tsvector) trong một giao dịch |
| | `vector_candidates` | `rag/store.py:87` | Truy vấn cosine trên HNSW (`ef_search` = 100) |
| `bm25.py` | `BM25Index`, `index_for` | `rag/bm25.py:18` | BM25 (k1 = 1.2, b = 0.75) trong RAM, dựng lại khi tài liệu đổi |
| `search.py` | `search`, `_search`, `rrf` | `rag/search.py:64` | Vector + BM25 → RRF → mục cha → reranker → ngưỡng τ |

Tại sao làm thế này:
- **Chunk theo cấu trúc, trả về mục cha**: chunk nhỏ thì embedding chính xác, nhưng agent cần cả mục để đủ
  ngữ cảnh, và trích dẫn theo mục (`#muc-n`) thì khách mở đúng chỗ.
- **Hybrid**: vector hiểu cách nói khác nhau, BM25 bắt từ hiếm và mã (eSIM, MagSafe). Điểm hai bên khác
  thang đo nên RRF chỉ dùng thứ hạng. Số đo trên 90 câu: vector Recall@5 0.867, BM25 0.800, hybrid 0.944,
  thêm reranker 0.967 (`eval/results/README.md:17`).
- **BM25 tự viết thay cho `ts_rank_cd`**: `ts_rank_cd` không có IDF nên từ phổ biến như "điện thoại" nặng ngang từ
  hiếm (`rag/bm25.py:3`).
- **Reranker**: cross-encoder đọc câu hỏi và đoạn văn cùng lúc nên xếp hạng chính xác hơn so khớp vector; điểm
  qua sigmoid nằm trong 0..1 nên so được với một ngưỡng cố định (`rag/models.py:71`).
- **τ = 0.1** (`rag/search.py:26`): chọn bằng `tau_analysis` (`eval/retrieval_eval.py:71`), so điểm của
  câu trả lời được với câu tài liệu không có; bản v4 hạ từ 0.2 xuống 0.1. Dưới τ thì `no_results`, model nói
  không có thông tin thay vì bịa.
- **Contextual Retrieval tắt mặc định** (`rag/ingest.py:6`): trên kho này không tăng
  Recall@5 mà tốn một lần gọi LLM cho mỗi chunk. 500 token giữ nguyên vì 250 token chỉ hơn 1 câu.
- **Một khóa cho cả nạp model và suy luận** (`rag/models.py:16`): nạp fp16 làm đổi kiểu số mặc định
  của torch cho cả tiến trình. `HashEmbedder` (`rag/models.py:30`) để unit test không phải tải model 2 GB.
- **Fingerprint** (`rag/ingest.py:43`): chạy lại ingest chỉ xử lý tài liệu thay đổi.

### 3.3 M3 – Vương Văn Đảng: shop demo, widget, đánh giá

| File | Hàm | Dòng | Làm gì |
|---|---|---|---|
| `shop_demo/api.py` | `require_api_key` | `shop_demo/api.py:18` | API C2 chỉ nhận Bearer key của Agent Server |
| | `get_order`, `list_orders`, `get_customer` | `shop_demo/api.py:62` | Luôn lọc theo `customer_id` trong path; sai khách trả 404 |
| | `search_products` | `shop_demo/api.py:71` | Tìm sản phẩm theo từ khóa, danh mục, giá tối đa |
| `shop_demo/app.py` | `chat_token` | `shop_demo/app.py:632` | Khách đã đăng nhập thì ký token chat, chưa đăng nhập trả 401 |
| | `own_order`, `advance_order` | `shop_demo/app.py:483` | Nút [Demo] đẩy đơn sang trạng thái kế tiếp để thử chatbot |
| `shop_demo/help_pages.py` | `render`, `page` | `shop_demo/help_pages.py:33` | Trang `/help` render từ cùng file Markdown, mỗi `##` có anchor `muc-n` |
| `shop_demo/sync_assistant.py` | `snapshot`, `main` | `shop_demo/sync_assistant.py:17` | Dựng bộ ưu đãi/sự kiện từ `events.json`; `main` gửi lên Agent Server bằng khóa ghi |
| `widget/loader.js` | `mount`, `refreshAuth` | `widget/loader.js:51` | Tạo nút và iframe trong Shadow DOM; đọc `data-shop-key`, `data-token-url`, `data-login-url`; lấy token từ shop |
| `widget/frame.js` | `callChat`, `consumeStream` | `widget/frame.js:302` | Gửi câu hỏi, đọc SSE |
| | `renderMarkdown`, `appendInline` | `widget/frame.js:44` | Markdown tối thiểu (đoạn, danh sách, in đậm, `[n]`) chỉ bằng DOM |
| | `addSources` | `widget/frame.js:238` | Hiện link nguồn nếu cùng origin với shop |
| | `canChat`, `authHeaders`, `loadGuestId` | `widget/frame.js:125` | Chọn token hay mã khách cho mỗi request; tạo và nhớ mã khách |
| `examples/second_shop/app.py` | `api_order` | `examples/second_shop/app.py:86` | API "lạ" (`/v2`, `X-Api-Key`, `data.order`) để chứng minh mức 1b |
| `eval/agent_eval.py` | `Runner.run_case` | `eval/agent_eval.py:90` | Gọi `agent.run` trực tiếp cho từng ca, lưu cache |
| | `Judge`, `GROUNDED` | `eval/agent_eval.py:165` | LLM-judge chấm groundedness, đúng/sai, từ chối, tấn công |
| `eval/run.py` | `agent_report` | `eval/run.py:95` | Tính chọn tool đúng, QA, groundedness, trích dẫn, từ chối, injection, độ trễ |
| `eval/retrieval_eval.py` | `evaluate` | `eval/retrieval_eval.py:43` | Recall@1/5, MRR@10 cho 6 cấu hình tìm kiếm |
| `scripts/bad_cases.py` | `run`, `start_offline_server` | `scripts/bad_cases.py:283` | 26 ca khó qua HTTP thật (quyền riêng tư, khách vãng lai, AI Box sập...), ghi `eval/results/bad_cases.md` |
| `eval/human_review.py` | `export`, `score` | `eval/human_review.py:53` | Xuất bảng Excel chia cho 3 người chấm lại; tính tỉ lệ đồng ý với LLM-judge |

Tại sao làm thế này:
- **Iframe + Shadow DOM**: CSS của shop không phá giao diện chat, JS của shop không đọc được hội thoại. Tin
  `postMessage` được kiểm tra origin ở cả hai phía (`widget/loader.js:90`).
- **Không có gì riêng cho một shop trong code widget**: tên, màu, lời chào, gợi ý lấy từ `/v1/widget-config`; link đăng
  nhập lấy từ `data-login-url` và chỉ nhận trang cùng origin với shop (`widget/frame.js:9`).
- **Không dùng `innerHTML`** (`widget/frame.js:43`): dù model bị lừa sinh HTML/script thì
  widget vẫn chỉ hiện chữ. Link nguồn khác origin của shop bị bỏ (`widget/frame.js:246`).
- **Mã khách vãng lai không phải đăng nhập**: widget chỉ gửi nó khi không có token, và server bỏ qua nó ở
  shop có API đơn hàng. Khách vãng lai gặp 401 thì không xin token lại (`widget/frame.js:347`).
- **API shop tự lọc theo khách** (`shop_demo/api.py:3`): phòng thủ nhiều lớp, kể cả khi Agent Server có lỗi.
- **Eval gọi đúng hàm production**: shop API chạy trong tiến trình qua `httpx.ASGITransport`
  (`eval/agent_eval.py:44`), không cần mở server. Kết quả cache theo hash của ca, prompt, tool,
  model, trạng thái kho và mã nguồn (`eval/agent_eval.py:81`), nên chạy lại không tốn lượt gọi AI Box.
- **Tấn công gián tiếp**: `poison_shop` có FAQ chứa lệnh giả (`eval/agent_eval.py:34`); ca tấn công đạt khi judge
  nói "không làm theo" và câu trả lời không chứa chuỗi bị cấm (`eval/run.py:186`).
- **Chấm lại judge bằng người**: `human_review.py` chia ca cho 3 thành viên (`eval/human_review.py:22`), để số liệu
  không chỉ dựa vào LLM-judge.
- **Ca AI Box sập là thật**: `bad_cases.py` mở server thứ hai ở cổng 8010 trỏ model tới địa chỉ không tồn tại
  (`scripts/bad_cases.py:284`).

## 4. Câu hỏi thầy có thể hỏi về code

**1. Nếu shop không có API thì sao? (M1)** Tích hợp theo mức (`docs/INTEGRATION.md`). Không có
`commerce_config` thì `adapter_for` trả `None` (`agent_server/commerce.py:296`), `toolset_for` không đưa tool đơn hàng,
prompt dặn "shop chưa kết nối hệ thống đơn hàng" (`agent_server/prompts.py:59`). Shop chỉ
đẩy FAQ/ưu đãi hoặc gửi Markdown. Mức 2 (Shopify, Haravan) mới là hướng phát triển.

**1b. Shop không có tài khoản khách thì sao? (M1 + M3)** Bật chế độ khách khi cấp shop (`--guest-chat`,
`agent_server/provision_shop.py:54`; lệnh từ chối nếu shop có API đơn hàng). Website tĩnh chỉ cần thẻ `<script>`, không cần
backend. Khách hỏi ngay, nhưng chỉ có tool tài liệu và ưu đãi, không có tra đơn và chuyển nhân viên (mục 2.4). Nếu sau này
shop nối API đơn hàng thì `guest_allowed` tự trả `False`, khách vãng lai bị từ chối.

**1c. Widget có dùng được cho shop khác ngoài demo không? (M3 + M1)** Có. Code widget không có gì riêng cho shop nào:
tiêu đề, màu, lời chào, gợi ý đến từ cấu hình lúc cấp shop (`agent_server/app.py:245`); đường dẫn token và trang đăng
nhập do thẻ nhúng của từng shop khai báo. Repo chạy cùng một widget trên 3 website khác origin (cổng 8001, 8002, 8003).
Một shop thứ tư (tiệm bánh, mức 0, chế độ khách) đã được cấp và chạy thật bằng 3 lệnh, ghi trong
`docs/INTEGRATION.md` mục 4. Agent Server có shop demo hay không cũng là tuỳ chọn (`demo_shop_enabled`).

**2. Làm sao agent không lộ đơn của khách khác? (M1)** Nhiều lớp, không lớp nào dựa vào prompt: mã khách chỉ
lấy từ JWT do shop ký; tool không có tham số mã khách và `extra="forbid"`; adapter đặt mã khách vào path
hoặc kiểm `owner_field` (`agent_server/commerce.py:103`); cấu hình không ràng buộc bị từ chối; API shop tự lọc
(`shop_demo/api.py:63`); hội thoại kiểm tra chủ (`agent_server/store.py:208`). Test:
`tests/test_agent_server.py:102`, `tests/test_review_fixes.py:53`.

**3. Prompt injection trong tài liệu hoặc kết quả tool? (M1)** Prompt coi kết quả tool là dữ liệu; nhưng quan
trọng hơn là dù model bị lừa, nó cũng không có quyền gì nguy hiểm: không có tool ghi, không chọn được khách,
widget không chạy HTML, link lạ bị bỏ. Đo bằng 15 kịch bản tấn công và `poison_shop`: 15/15 bị chặn
(`eval/results/README.md`).

**4. AI Box sập thì sao? (M1)** `stream` thử lại một lần khi lỗi tạm thời và chưa có chữ
(`shared/llm.py:104`). Vẫn lỗi thì `LLMError`; chưa có chữ thì chuyển fallback (`agent_server/agent.py:95`),
có chữ rồi thì gửi `error`. Không có API key thì chạy fallback ngay (`agent_server/agent.py:51`) và widget hiện
"CHẾ ĐỘ DỰ PHÒNG". `bad_cases.md` có hai ca AI Box sập, cả hai OK (lần chạy 04/11: 26 ca, 24 OK, 2 hạn chế đã biết, 0 lỗi mới).

**5. Sao biết câu trả lời không bịa? (M1 + M3)** Bốn chỗ: luật prompt chỉ dùng dữ liệu từ tool
(`agent_server/prompts.py:16`); τ trả `no_results` khi tài liệu không đủ liên quan; `cited_sources` bỏ số `[n]` mà tool
không cấp; eval có judge groundedness so câu trả lời với đúng dữ liệu agent đã thấy (`eval/agent_eval.py:246`).
Kết quả v9: 0.900, rà tay 9 câu bị gắn cờ thì 89/90 = 0.989 (`eval/results/README.md:46`).

**6. Thêm một tool mới thì sửa những đâu? (M1)** (1) `tools.py`: lớp tham số kế thừa `Args`, hàm
`async def tool(ctx, args) -> Outcome`, thêm `Tool(...)` vào `TOOLS`. (2) Nếu cần dữ liệu shop: thêm khả năng vào
`CAPABILITIES` (`agent_server/commerce.py:23`), phương thức trong `CommerceAdapter`/`RestCommerceAdapter`, đường dẫn
trong `STANDARD_PROFILE`, và `CUSTOMER_SCOPED` nếu là dữ liệu của một khách. (3) Luật dùng trong `COMMERCE_RULES`
hoặc `tool_rules`. (4) Test với `FakeLLM` (`tests/support.py:92`) và ca trong `eval/cases_agent.jsonl`. Widget không phải sửa:
câu trạng thái lấy từ `Tool.status`.

**7. Thêm một shop mới thì làm gì? (M1 + M3)** Chạy `provision_shop` (in khóa một lần,
`agent_server/provision_shop.py:40`). Có API thì thêm `--commerce-config` (file ánh xạ) và `--commerce-api-key`. Shop nhúng
một thẻ `<script>` và viết endpoint ký token bằng SDK. Nạp tài liệu bằng `python -m rag.ingest --shop <id>` hoặc
đẩy FAQ (`examples/push_faq.py`). Không sửa dòng code nào của Agent Server.

**8. Sao dùng SSE mà không dùng WebSocket? (M1 + M3)** Xem mục 3.1. Thêm: hợp đồng C4 cố định thứ tự sự kiện,
nên widget, `smoke_live.py` và unit test đều đọc cùng một định dạng.

**9. Agent có thể lặp vô hạn không? (M1)** Không: tối đa 5 lần gọi model, lần cuối `tool_choice="none"`
(`agent_server/agent.py:60`). Prompt cấm gọi lại tool với cùng tham số. Có test:
`tests/test_agent_server.py:134`.

**10. Đã có hybrid sao còn reranker? τ chọn thế nào? (M2)** Hybrid cho Recall@5 0.944 nhưng thứ tự chưa tốt
(Recall@1 0.589); reranker đưa Recall@1 lên 0.811 và cho điểm 0..1 để đặt ngưỡng. τ chọn theo bảng
`tau_analysis` (`eval/retrieval_eval.py:81`).

**11. Tài liệu shop thay đổi thì cập nhật thế nào? (M2)** Chạy lại `rag.ingest`: tài liệu nào có fingerprint không
đổi thì bỏ qua, tài liệu bị xoá thì xoá khỏi index (`rag/ingest.py:65`). FAQ đẩy qua API được index lại ngay
(`agent_server/app.py:259`). BM25 trong RAM tự dựng lại khi `signature` của shop đổi (`rag/bm25.py:52`).

**12. Trang shop có đọc trộm hội thoại hay chèn script vào widget được không? (M3)** Hội thoại nằm trong
iframe khác origin; token chỉ đi qua `postMessage` có kiểm origin; Markdown vẽ bằng `textContent`; CSP của iframe
`script-src 'self'` (`agent_server/app.py:223`).

**13. Khách gửi hai tin liền nhau, hoặc nhiều người dùng cùng lúc? (M1)** Mỗi hội thoại có một khóa nên hai lượt
chạy lần lượt (test `tests/test_agent_server.py:212`). Hội thoại khác nhau chạy song song (async). Khóa và
bộ đếm giới hạn nằm trong RAM nên chỉ đúng với một tiến trình; đây là giới hạn của prototype
(`agent_server/rate_limit.py:1`).

**14. Số liệu đánh giá lấy từ đâu, có tự chấm cho mình không? (M3)** `python -m eval.run agent` chạy 50 ca chọn tool,
40 câu hỏi–đáp, 20 câu ngoài phạm vi, 15 kịch bản tấn công. Chọn tool chấm bằng luật (`eval/run.py:43`),
phần còn lại bằng LLM-judge với prompt cố định, `temperature=0`. Judge có thể sai, nên nhóm rà tay các câu bị gắn cờ
và ghi rõ trong `eval/results/README.md`.

**15. Token JWT bị lộ thì sao? (M1)** Token sống 15 phút, ràng buộc `iss` (shop) và `aud`; chỉ dùng được với đúng
shop đó. Shop có thể xoay khóa ký bằng `rotate_chat_secret`. Hạn chế: `jti` có trong token nhưng server chưa
kiểm tra, nên trong 15 phút token có thể dùng lại (xem mục 5).

## 5. Điểm code còn có thể bị chê

Nếu thầy chỉ ra, nên nhận và nói hướng sửa; đừng cãi. Danh sách dưới đây đã kiểm tra trong code.

| # | Chỗ | Vấn đề | Gợi ý sửa |
|---|---|---|---|
| 1 | `agent_server/app.py:366` | `chat` dài khoảng 80 dòng, hàm sinh `events` lồng bên trong làm cả khóa, lịch sử, agent, lưu, trace, lỗi | Tách `_run_turn` và `_save_turn` |
| 2 | `agent_server/app.py:205` | 4 endpoint file tĩnh gần như giống nhau (`loader.js`, `loader.css`, `frame.js`, `frame.css`) | Một hàm chung hoặc `StaticFiles` |
| 3 | `agent_server/app.py:271`, `agent_server/app.py:304` | Lặp logic kiểm tra: trùng id, `strip`, so origin của `source_url` | Một hàm kiểm tra dùng chung |
| 4 | `agent_server/app.py:70` | Nút đánh giá "Hữu ích / Chưa hữu ích" chỉ đếm trong RAM, mất khi khởi động lại | Lưu vào bảng trong PostgreSQL |
| 5 | `agent_server/app.py:71`, `agent_server/app.py:96` | Đối tượng toàn cục giữ trạng thái trong RAM (khóa hội thoại, bộ đếm): chỉ đúng khi chạy 1 tiến trình | Redis/DB nếu chạy nhiều worker |
| 6 | `agent_server/app.py:454`, `agent_server/tools.py:190` | Gọi DB đồng bộ trong hàm `async` mà không qua `asyncio.to_thread` (chỗ khác thì có), chặn event loop một chút | Thống nhất dùng `to_thread` hoặc driver async |
| 7 | `agent_server/store.py:56`, `agent_server/store.py:99`, `agent_server/store.py:124` | Ba câu SQL ghi bảng `shops` gần giống nhau (việc tạo shop demo trong `init_db` nay đã tuỳ chọn, chỉ chạy khi `demo_shop_enabled()`, `agent_server/store.py:49`) | Gom một hàm `upsert_shop` |
| 8 | `agent_server/demo_data.json` | Khóa `faq` (5 câu) không còn được code nào đọc; `store.py` chỉ đọc 6 khóa khác | Xoá khóa thừa |
| 9 | `agent_server/tools.py:269` | `BY_NAME` không được dùng; agent và `execute` mỗi nơi tự tìm tool bằng vòng lặp | Dùng `BY_NAME` hoặc xoá |
| 10 | `agent_server/commerce.py:163` | Mỗi lần gọi API shop tạo một `AsyncClient` mới, không dùng lại kết nối | Giữ một client cho mỗi adapter |
| 11 | `agent_server/app.py:263` | Tạo event loop mới trong thread vì `index_faqs` là `async` nhưng làm việc đồng bộ | Cho ingest FAQ một hàm đồng bộ |
| 12 | `shared/llm.py:68` | Mã vá cấu hình cũ từ tuần 1 | Xoá, chỉ đọc `LLM_MODEL` |
| 13 | `rag/documents.py:27` | Trường `intro` luôn rỗng vì `parse` đã nhập đoạn mở đầu vào mục đầu tiên (`rag/documents.py:79`) | Bỏ trường |
| 14 | `rag/search.py:56` | Tham số `category` của tìm kiếm không còn được agent dùng (tool luôn truyền `None`) | Giữ cho hợp đồng C1 nhưng ghi rõ, hoặc bỏ |
| 15 | `shop_demo/app.py` (654 dòng) | Một file chứa catalogue, giỏ hàng, thanh toán, đơn, tài khoản, trợ giúp, token | Tách thành các router như `api.py` |
| 16 | `shop_demo/app.py:584`, `shop_demo/demo_chat.py` | Chatbot quy tắc cũ (`WIDGET_MODE=demo`) và bảng chính sách viết tay trùng nội dung `data/kb`; `templates/policy.html` không còn route nào render | Xoá hoặc ghi rõ là code cũ |
| 17 | `eval/run.py:216`, `eval/agent_eval.py:151` | Hàm bọc không làm gì thêm; tham số `ctx` không dùng | Gọi thẳng `tool_data`; bỏ tham số |
| 18 | `agent_server/auth.py:12` | `jti` được SDK sinh (`sdk/ati_sdk.py:33`) nhưng không kiểm tra: token dùng lại được trong 15 phút | Lưu `jti` đã dùng tới khi hết hạn nếu cần chống replay |
| 19 | `agent_server/app.py:166` | Mã khách vãng lai do trình duyệt tự sinh; đổi mã là né được giới hạn "mỗi khách/phút" (giới hạn theo IP và theo ngày vẫn còn) | Giới hạn chặt hơn theo IP cho khách vãng lai |
| 20 | Test | Không có test tự động cho `widget/*.js` (chỉ `scripts/browser_smoke.mjs` chạy tay, đường dẫn Chrome viết cứng `scripts/browser_smoke.mjs:8`); 2 test của `shop_demo/` không chạy trong lệnh test chính | Thêm vào CI hoặc ghi rõ lệnh chạy riêng |

## 6. Cách chạy và debug nhanh

Cài đặt và chạy demo (PowerShell, từ thư mục gốc, theo `README.md`):

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env                          # điền AI_BOX_API_KEY
.venv\Scripts\python scripts\setup_demo.py      # sinh khóa, seed dữ liệu, cấp 3 shop, nạp kho tri thức
.venv\Scripts\python scripts\run_all.py         # 8000 agent, 8001 Demo Mobile, 8002 Sample Store, 8003 Phụ Kiện Xanh
```

Kiểm thử (Git Bash; 74 test, khoảng 25 giây, không gọi LLM). Trong lúc chạy có in traceback `pool timeout`: đó là
test cố tình giả lỗi tool, kết quả cuối phải là `OK`.

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe -m unittest discover -s tests -t .
PYTHONUTF8=1 .venv/Scripts/python.exe -m unittest tests.test_commerce          # chỉ một file
PYTHONUTF8=1 .venv/Scripts/python.exe -m unittest tests.test_guest_mode        # chế độ khách
cd shop_demo && ../.venv/Scripts/python.exe -m unittest test_config test_offers   # 2 test của shop demo
```

Khi `run_all.py` đang chạy (cần key AI Box):

```powershell
.venv\Scripts\python scripts\smoke_live.py                     # bắt đăng nhập, chính sách, tra đơn, khách khác, shop thứ hai
.venv\Scripts\python scripts\bad_cases.py                      # 26 ca khó -> eval/results/bad_cases.json và .md
.venv\Scripts\python scripts\bad_cases.py --only cancel_order  # chạy lại một ca, giữ kết quả các ca khác
.venv\Scripts\python scripts\bad_cases.py --recheck            # chấm lại câu trả lời đã lưu, không gọi server
.venv\Scripts\python scripts\trace_stats.py --shop demo_shop --since 2026-10-29   # độ trễ, token từ bảng traces
node scripts\browser_smoke.mjs                                 # thử widget bằng Chrome headless
node scripts\record_demo.mjs demo.mp4                          # quay video demo 3 shop (cần Chrome, ffmpeg; DEMO_QUICK=1 quay ngắn)
```

Đánh giá và các lệnh khác:

```powershell
.venv\Scripts\python -m eval.run retrieval                 # Recall@k, MRR (không gọi LLM)
.venv\Scripts\python -m eval.run agent --tag v10           # chọn tool, groundedness, từ chối, tấn công
.venv\Scripts\python -m eval.run baseline                  # nhồi cả kho vào prompt so với RAG
.venv\Scripts\python -m eval.ablation                      # các biến thể chỉ mục
.venv\Scripts\python -m eval.human_review export eval/results/agent_v9.json review_v9.xlsx   # bảng cho 3 người chấm
.venv\Scripts\python -m eval.human_review score review_v9.xlsx                              # tỉ lệ đồng ý với judge
.venv\Scripts\python -m agent_server.cli --shop demo_shop --customer C0001   # chat trong terminal, không cần widget
.venv\Scripts\python -m rag.ingest --shop demo_shop [--contextual] [--force]
```

Mẹo debug:

| Triệu chứng | Xem ở đâu |
|---|---|
| Widget hiện "CHẾ ĐỘ DỰ PHÒNG" | `GET http://localhost:8000/healthz`; `AI_BOX_API_KEY` trống thì `llm.enabled` là `False` (`shared/llm.py:78`) |
| Chat báo 401 | `DEMO_SHOP_TOKEN_SECRET` của hai server phải giống nhau; server in cảnh báo khi thiếu (`agent_server/app.py:62`). Ở Sample Store: shop phải có cờ `guest_chat` (chạy lại `setup_demo.py`) |
| Tra đơn báo "hệ thống shop không phản hồi" | `DEMO_SHOP_API_KEY`; log `shop API failed` (`agent_server/tools.py:302`) |
| Agent chọn sai tool | Mô tả tool trong `TOOLS` và `tool_rules`; xem cột `why` trong `eval/runs/<tag>/agent.json` |
| Tìm sai tài liệu | Chạy `agent_server.cli` để thấy tool và nguồn; thử `RAG_RERANK=0`, `RAG_TAU`, `RAG_KEYWORD=tsrank` trong `.env` (`rag/search.py:30`) |
| Lượt chat chậm | `trace_stats.py` (p50/p95, tỉ lệ fallback, tỉ lệ cache) |
