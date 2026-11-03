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
| `tests/` | Unit test chạy với embedder giả, không gọi LLM | Chung: `test_agent_server`, `test_commerce`, `test_resilience`, `test_guest_mode`, `test_logout` (M1), `test_rag` (M2), `test_review_fixes`, `test_final_review` (cả nhóm) |
| `docs/`, `README.md`, `Dockerfile`, `docker-compose.yml` | Hợp đồng C1–C6, hướng dẫn tích hợp, chạy bằng Docker | Chung |

### Ranh giới giữa các phần (trả lời câu "kiến trúc có sạch không")

- Agent core không biết HTTP hay widget (`agent_server/agent.py:4`). Cùng
  một hàm `agent.run` được gọi từ API chat (`agent_server/app.py:528`), từ
  terminal (`agent_server/cli.py:31`) và từ bộ eval
  (`eval/agent_eval.py:119`).
- Chỉ `commerce.py` nói chuyện với API của shop; chỉ `rag/search.py` biết cách tìm tài liệu. Agent chỉ
  thấy hai thứ đó qua `TurnContext.adapter` và `TurnContext.search` (`agent_server/tools.py:71`).
- Mỗi ranh giới có hợp đồng viết ra giấy: `docs/contracts.md` (C1 tìm kiếm, C2 API shop, C3 JWT, C4 SSE,
  C5 agent, C6 bộ eval).

## 2. Một câu hỏi đi qua code như thế nào

### 2.1 Trước khi hỏi: widget lấy token đăng nhập

| Bước | Ở đâu | Chuyện gì xảy ra |
|---|---|---|
| 1 | `shop_demo/templates/base.html:84` | Trang shop nhúng `loader.js` với `data-shop-key`, `data-token-url` và (không bắt buộc) `data-login-url` |
| 2 | `widget/loader.js:55` | Tạo Shadow DOM và iframe `/widget/frame?shop_key=...&parent_origin=...` (`widget/loader.js:72`); thêm `login_url` nếu trang đăng nhập cùng origin với shop (`widget/loader.js:76`) |
| 3 | `agent_server/app.py:317` | Agent Server chỉ trả iframe nếu `parent_origin` nằm trong `allowed_origins` của shop, và đặt CSP `frame-ancestors` (`agent_server/app.py:324`) |
| 4 | `widget/frame.js:520` | iframe tải `/v1/widget-config`, báo `ati:ready` cho trang cha |
| 5 | `widget/loader.js:23` | `loader.js` gọi `/api/chat-token` của shop (bắt buộc cùng origin, `widget/loader.js:31`) |
| 6 | `shop_demo/app.py:702` → `shop_demo/security.py:30` → `sdk/ati_sdk.py:32` | Shop ký JWT HS256 (`iss`=shop, `sub`=mã khách, `aud`, `exp` sau 900 s) |
| 7 | `widget/frame.js:441` | iframe nhận `ati:auth` (chỉ từ đúng trang cha, `widget/frame.js:442`) và giữ token trong bộ nhớ; chưa đăng nhập thì hiện link tới `login_url` của shop (`widget/frame.js:176`) |
| 8 | `shop_demo/templates/base.html:32` → `widget/frame.js:461`; `shop_demo/app.py:418` | Khi khách đăng xuất: trang gọi `ATI.reset()`, widget gửi `POST /v1/logout` với token hiện tại; backend shop cũng gọi `POST /v1/shops/{id}/logout` bằng khóa ghi. Mọi token đã cấp cho khách đó trước lúc này bị từ chối |

### 2.2 "Đơn SO10293 của tôi tới đâu rồi?"

1. **Widget gửi câu hỏi.** `send` (`widget/frame.js:402`) vẽ bong bóng của khách và một
   bong bóng trống cho bot, rồi `callChat` (`widget/frame.js:384`) gửi
   `POST /v1/chat` với `Authorization: Bearer <JWT>` và body `{shop_key, message, conversation_id}`. Mọi request đi
   qua `apiFetch` (`widget/frame.js:254`): gặp 401 thì xin trang shop token mới và thử lại một lần;
   gặp 429 thì báo khách chờ.
2. **Agent Server kiểm tra.** `chat` (`agent_server/app.py:491`) lần lượt:
   - `require_widget_origin` (`agent_server/app.py:284`): trình duyệt luôn gửi `Origin` với POST,
     và nó phải là origin của Agent Server, tức là chỉ iframe widget được gọi.
   - `require_shop_key` (`agent_server/app.py:242`): tìm shop theo public key.
   - `require_customer` (`agent_server/app.py:266`) gọi `verify_customer_claims`
     (`agent_server/auth.py:21`): kiểm tra `alg`, chữ ký, `iss` = shop, `aud`,
     `exp`, `iat`, thời gian sống tối đa 1 giờ; rồi `token_revoked` từ chối token cấp trước lần đăng xuất gần
     nhất của khách (`agent_server/app.py:276`). Mã khách lấy từ `sub` của token. Body không được có `customer_id`
     (`agent_server/app.py:173`).
   - Hội thoại cũ phải thuộc đúng khách (`agent_server/app.py:502`).
   - Giới hạn tần suất (`agent_server/app.py:505` → `agent_server/rate_limit.py:45`).
3. **Mở luồng SSE.** Hàm sinh `events` (`agent_server/app.py:512`) giữ khóa của hội thoại
   (`agent_server/app.py:518`); giới hạn thời gian của lượt chỉ tính từ lúc có
   khóa (`agent_server/app.py:519`). Sau đó đọc 8 tin gần nhất hoặc tạo hội
   thoại mới, rồi dựng `TurnContext` (`agent_server/app.py:484`) với adapter của shop
   (`agent_server/commerce.py:354`) và hàm `search`.
4. **Agent chuẩn bị.** `run` (`agent_server/agent.py:54`) chọn bộ tool theo khả năng của shop
   (`agent_server/tools.py:289`), rồi ghép theo thứ tự: system prompt (`agent_server/prompts.py:88`),
   lịch sử, tin nhắn ngày giờ (`agent_server/prompts.py:98`) và câu hỏi, rồi phát `status`.
5. **Lượt gọi model thứ nhất.** Vòng lặp (`agent_server/agent.py:76`) gọi
   `LLMProvider.stream` (`shared/llm.py:93`). LiteLLM gửi tới DeepSeek qua AI Box; chữ được
   đẩy ra từng mẩu, còn lời gọi tool được ghép theo `index` (`shared/llm.py:124`).
   Model chọn `get_order_status` với `{"order_id": "SO10293"}`; theo luật prompt, nó không viết chữ nào trước khi gọi tool.
6. **Chạy tool.** Agent ghi lời gọi tool vào `messages` (`agent_server/agent.py:95`),
   phát `status` "Đang kiểm tra đơn hàng...", rồi chạy các lời gọi cùng lúc (`agent_server/agent.py:112`);
   lời gọi trùng trong một lượt chỉ chạy một lần. Mỗi lời gọi qua `execute` (`agent_server/tools.py:302`):
   parse JSON, bỏ giá trị `null`, kiểm tra bằng `OrderArgs` (`agent_server/tools.py:39`,
   regex cho mã đơn, số được đọc thành chữ, cấm trường lạ).
7. **Gọi API của shop với mã khách từ token.** `get_order_status` (`agent_server/tools.py:155`)
   gọi `ctx.adapter.get_order(args.order_id, ctx.customer_id)`. `RestCommerceAdapter.get_order`
   (`agent_server/commerce.py:286`) dùng `_get` (`agent_server/commerce.py:186`):
   mã hoá URL các biến, điền vào path `/api/v1/customers/{customer_id}/orders/{order_id}`
   (`agent_server/commerce.py:43`) bằng `fill` (`agent_server/commerce.py:86`), gửi GET kèm API key,
   không theo redirect, có hạn giờ cho cả lần gọi (`agent_server/commerce.py:206`). 404 thành `None`; mã khác 200
   thành `CommerceError`. Sau đó `_owned` (`agent_server/commerce.py:240`) kiểm tra `owner_field` nếu có, `_order`
   (`agent_server/commerce.py:269`) đổi tên trường, đổi trạng thái về 6 giá trị chuẩn, gắn link trang đơn, và `same_id`
   (`agent_server/commerce.py:294`) bỏ đơn có mã khác mã khách hỏi (shop tìm "gần đúng").
8. **Phía shop.** `get_order` của shop demo (`shop_demo/api.py:62`) chỉ tìm
   `WHERE id = ? AND customer_id = ?`, nên đơn của khách khác trả 404 giống hệt đơn không tồn tại.
9. **Kết quả về model.** Tool thêm link "Xem đơn SO10293" vào `ctx.links` (`agent_server/tools.py:160`)
   và chỉ đưa cho model các trường cần (`agent_server/tools.py:144`). Agent thêm một tin
   `role: tool` chứa JSON (`agent_server/agent.py:117`) rồi quay lại vòng lặp.
10. **Lượt gọi model thứ hai.** Model viết câu trả lời (trạng thái, ngày giao dự kiến, mã vận đơn). Mỗi mẩu
    chữ là một sự kiện `token`, được `app.py` đổi thành SSE (`agent_server/app.py:291`, `agent_server/app.py:539`).
11. **Kết thúc lượt.** `cited_sources` (`agent_server/agent.py:48`) gom nguồn; dữ liệu đơn không
    cần số `[n]` nên chỉ có link trang đơn. Agent phát `citations` rồi `done` (kèm `reply`, `tools`, `usage`).
12. **Lưu và trace.** `app.py` lưu câu hỏi và câu trả lời (`agent_server/store.py:270`), gửi SSE `done`
    (`agent_server/app.py:553`), rồi mới ghi trace chỉ gồm tên tool, tham số đã che và thời gian
    (`agent_server/app.py:474`, `agent_server/store.py:365`), nên khách không phải chờ việc ghi log. Có lỗi bất kỳ
    thì gửi `error` (`agent_server/app.py:564`), không bao giờ cắt luồng im lặng.
13. **Widget vẽ.** `consumeStream` (`widget/frame.js:341`) tách từng khối SSE
    (`widget/frame.js:329`): `status` hiện dòng trạng thái, `token` nối chữ và vẽ lại bằng
    `renderMarkdown` (`widget/frame.js:67`), `replace` thay toàn bộ bong bóng, `citations` gọi
    `addSources` (`widget/frame.js:275`, chỉ hiện link cùng origin với shop), `done` lưu `conversation_id`.

`scripts/smoke_live.py` kiểm tra đúng luồng này trên server thật: câu trả lời phải có mã vận đơn mà chỉ API
shop biết (`scripts/smoke_live.py:53`), và khách khác hỏi cùng mã đơn thì không thấy tổng tiền
(`scripts/smoke_live.py:58`).

### 2.3 Câu hỏi chính sách: "Phí giao hàng cho đơn 300 nghìn là bao nhiêu?"

1. Model chọn `search_knowledge_base` với một truy vấn tiếng Việt đầy đủ (mô tả tool yêu cầu như vậy,
   `agent_server/tools.py:243`).
2. `search_knowledge_base` (`agent_server/tools.py:129`) chạy `ctx.search` trong thread
   riêng, vì model embedding và reranker chạy đồng bộ.
3. `search` (`rag/search.py:53`) gọi `_search` (`rag/search.py:74`):
   - Nhánh vector: BGE-M3 mã hoá câu hỏi, lấy 20 chunk gần nhất theo cosine trên chỉ mục HNSW
     (`rag/search.py:80` → `rag/store.py:87`).
   - Nhánh từ khóa: `query_terms` (`shared/text.py:50`) bỏ dấu, bỏ hư từ, thêm cặp âm tiết
     (ví dụ `giao_hang`); BM25 trong RAM lấy 20 chunk (`rag/search.py:89`).
   - Gộp hai danh sách bằng RRF với k = 60 (`rag/search.py:41`).
   - Đổi chunk thành mục cha `##`, mỗi mục lấy điểm của chunk tốt nhất (`rag/search.py:95`),
     gắn link `/help/<slug>#muc-<n>` (`rag/search.py:112`).
   - Reranker `bge-reranker-v2-m3` chấm lại 10 mục đầu (`rerank_top`, `rag/search.py:30`) theo từng cặp
     (câu hỏi, mục) (`rag/search.py:124`), sắp xếp, giữ 5 mục.
4. Nếu điểm cao nhất nhỏ hơn τ = 0.1 thì trả `[]` (`rag/search.py:69`). Khi đó tool
   trả `no_results` kèm lời dặn "tìm lại bằng từ khác một lần, nếu vẫn không có thì đừng đoán"
   (`agent_server/tools.py:132`).
5. Có kết quả thì mỗi mục được đánh số bằng `ctx.cite` (`agent_server/tools.py:82`); mục tìm lại lần hai giữ số cũ. Model viết
   "... 30.000₫ [1]" theo luật trích dẫn (`agent_server/prompts.py:19`).
6. `cited_sources` chỉ giữ những số vừa xuất hiện trong câu trả lời vừa thật sự được tool cấp
   (`agent_server/agent.py:49`); `citation_numbers` đọc cả cách viết gộp `[1, 2]` và `[1-3]`
   (`agent_server/agent.py:30`). Widget hiện "[1] Tên tài liệu – Mục", link mở đúng mục trên trang
   trợ giúp do shop render với cùng anchor (`shop_demo/help_pages.py:41`).

### 2.4 Khách vãng lai ở shop mức 0 (Sample Store, cổng 8002)

Shop không có tài khoản khách thì không ký được JWT. Luồng khác ở bốn chỗ:

1. Trang tĩnh nhúng `loader.js` với `pk_sample_store` và không có `data-token-url`
   (`examples/third_party_site/index.html:25`), nên `loader.js` gửi `ati:auth` với token rỗng.
2. `/v1/widget-config` trả `guest_chat` (`agent_server/app.py:354`). Khi đó widget lấy mã khách
   (`widget/frame.js:150`): 32 ký tự hex ngẫu nhiên từ `crypto.getRandomValues`, lưu trong `localStorage`
   theo từng shop key. `authHeaders` (`widget/frame.js:141`) gửi `X-ATI-Guest` khi không có token.
3. `require_customer` (`agent_server/app.py:266`): có Bearer thì luôn dùng token; không có thì chỉ nhận
   mã khách nếu đúng định dạng và `guest_allowed(shop)` (`agent_server/store.py:172`): shop bật cờ
   `guest_chat` **và** không có `commerce_config`. Mã khách thành `customer_id = "guest:<mã>"`.
4. `TurnContext.guest` (`agent_server/tools.py:93`) báo cho agent. `toolset_for` bỏ tool đơn hàng và
   `escalate_to_human` (`agent_server/tools.py:294`); prompt nói đây là khách chưa đăng
   nhập và dặn đưa kênh liên hệ trong tài liệu thay vì chuyển nhân viên (`agent_server/prompts.py:82`).

Phần còn lại (RAG, SSE, lưu lịch sử theo `customer_id`) giống hệt mục 2.2 và 2.3. Test:
`tests/test_guest_mode.py:53`, `tests/test_guest_mode.py:73`.

## 3. Phần của từng thành viên

### 3.1 M1 – Nguyễn Tiến Anh: agent core, Agent Server, bảo mật

**`agent_server/agent.py`**

| Hàm | Dòng | Làm gì |
|---|---|---|
| `run` | `agent_server/agent.py:54` | Vòng lặp tối đa `MAX_ITERATIONS` = 5 lần gọi model (`agent_server/agent.py:25`); lần cuối ép `tool_choice="none"` để model phải trả lời |
| `history_messages` | `agent_server/agent.py:42` | Lấy 8 tin gần nhất, xoá số `[n]` cũ để không trùng số của lượt mới |
| `cited_sources`, `citation_numbers` | `agent_server/agent.py:48` | Giao của "số có trong câu trả lời" và "số tool đã cấp", cộng link đơn/sản phẩm |
| `no_answer_text` | `agent_server/agent.py:143` | Câu dự phòng khi model không viết gì; khách vãng lai không được đề nghị chuyển nhân viên |
| `valid_json` | `agent_server/agent.py:154` | Gửi lại tham số tool cho gateway; JSON hỏng thì thay bằng `{}` |
| `_fallback` | `agent_server/agent.py:163` | Gọi `fallback.answer`, phát lại như một lượt bình thường với `mode="fallback"` |

Tại sao làm thế này:
- **Có trần số vòng lặp**: không lặp vô hạn, chi phí mỗi câu có giới hạn; lần cuối không cho gọi tool.
- **Sự kiện `replace`**: prompt bắt gọi tool im lặng (`agent_server/prompts.py:16`), nhưng đôi khi
  model vẫn viết "Để mình kiểm tra..." rồi mới gọi tool. Chữ đó đã được stream tới khách, nên phải rút lại:
  `replace` mang câu trả lời tính tới trước lượt này (`agent_server/agent.py:91`).
- **Chuyển nhân viên kết thúc lượt bằng câu cố định** (`agent_server/agent.py:120`): lời hẹn liên hệ lại
  lấy từ cấu hình của shop (`handover_note`), nên model không thể tự hứa thời gian.
- **Ngày giờ đặt sau lịch sử** (`agent_server/agent.py:60`): system prompt, tool và các lượt cũ là phần đầu
  không đổi, gateway cache được.
- **Tool gọi cùng lúc chạy song song**: hỏi đơn và chính sách trong một câu thì hai lời gọi chạy đồng thời.
- **AI Box lỗi**: chưa có chữ nào thì chuyển sang fallback; đã có chữ thì báo `error`, không trộn hai kiểu trả
  lời trong một bong bóng (`agent_server/agent.py:124`).

**`agent_server/tools.py`**

| Hàm / lớp | Dòng | Làm gì |
|---|---|---|
| `Args` và các lớp con | `agent_server/tools.py:29` | Tham số tool, `extra="forbid"`: trường lạ như `customer_id` bị từ chối |
| `TurnContext` | `agent_server/tools.py:71` | Trạng thái một lượt: shop, mã khách (từ token), adapter, hàm search, nguồn trích dẫn, link, khách viết tiếng Anh hay không |
| `TOOLS` | `agent_server/tools.py:242` | 7 tool: tên, mô tả cho model, lớp tham số, câu trạng thái, khả năng cần có, hàm chạy |
| `toolset_for` | `agent_server/tools.py:289` | Chỉ đưa tool có khả năng thật; shop mức 0 và khách vãng lai không có tool đơn hàng |
| `execute` | `agent_server/tools.py:302` | Kiểm tra tham số và chạy; mọi lỗi thành dữ liệu cho model giải thích |
| `escalate_to_human` | `agent_server/tools.py:227` | Tạo phiếu (một phiếu cho mỗi lượt nhờ `turn_key`), trả câu cố định kèm lời hẹn của shop (`handover_note`, có bản tiếng Anh) |
| `list_customer_orders` | `agent_server/tools.py:164` | Trả `shown` và `may_have_more` để model không nói nhầm "bạn có 5 đơn" khi chỉ xem 5 đơn mới nhất |

Tại sao làm thế này:
- **Model không bao giờ nhập mã khách** (`agent_server/tools.py:3`). Không tool nào có tham
  số `customer_id`; nếu model bị lừa và tự thêm, Pydantic từ chối trước khi gọi shop (test:
  `tests/test_agent_server.py:94`).
- **Lỗi thành dữ liệu** (`shop_system_unavailable`, `tool_failed`, `agent_server/tools.py:318`): model xin
  lỗi và đề nghị chuyển nhân viên thay vì đoán, và lượt chat không bị chết.
- **Không có tool ghi, trừ `escalate_to_human`**: agent chỉ đọc. Nếu bị prompt injection, tệ nhất là trả lời
  sai chữ, không thể huỷ đơn hay hoàn tiền. Việc cần người làm thì đi qua phiếu cho nhân viên.
- **Bỏ tham số `category`** khỏi tool tìm kiếm (`agent_server/tools.py:34`): eval v2 cho thấy model đoán
  sai danh mục làm mất tài liệu đúng.
- **Che PII trước khi đưa cho model** (`agent_server/tools.py:178`): email, số điện thoại, địa chỉ.

**`agent_server/prompts.py`**

| Hàm | Dòng | Làm gì |
|---|---|---|
| `BASE` | `agent_server/prompts.py:10` | Luật trả lời, luật trích dẫn, luật an toàn |
| `tool_rules` | `agent_server/prompts.py:51` | Sinh luật dùng tool theo bộ tool thật của shop |
| `system_prompt` | `agent_server/prompts.py:88` | Ghép tên shop, giọng điệu, luật tool |
| `context_message` | `agent_server/prompts.py:98` | Ngày giờ Việt Nam; nếu khách viết tiếng Anh thì dặn trả lời tiếng Anh |

Tại sao làm thế này:
- **Phần đầu prompt không đổi giữa các lượt** (`agent_server/prompts.py:2`) để AI Box cache được;
  ngày giờ nằm ở tin nhắn riêng, sau lịch sử.
- **Kết quả tool là DỮ LIỆU, không phải lệnh** (`agent_server/prompts.py:29`): tài liệu và FAQ
  do shop đẩy lên, dữ liệu đơn có chữ do khách gõ, nên đều có thể chứa câu giả lệnh. Prompt chỉ là lớp mềm;
  lớp cứng nằm trong code (bộ tool, ràng buộc theo khách, che PII).
- **Phát hiện tiếng Anh bằng code** (`agent_server/prompts.py:101`): chỉ ghi luật trong prompt thì thua kết quả
  tool tiếng Việt (eval v8).

**`agent_server/app.py` (cùng `auth.py`, `rate_limit.py`)**

| Hàm / lớp | Dòng | Làm gì |
|---|---|---|
| `chat` / `events` | `agent_server/app.py:491` | Xác thực, giới hạn, chạy agent, đổi sự kiện thành SSE, lưu, trace |
| `ConversationLocks` | `agent_server/app.py:132` | Một khóa cho mỗi hội thoại: tin thứ hai chờ tin thứ nhất lưu xong |
| `require_customer` | `agent_server/app.py:266` | Mã khách từ JWT; shop mức 0 bật `guest_chat` thì nhận khách vãng lai |
| `require_shop_key` | `agent_server/app.py:242` | Tìm shop theo public key, cache 10 giây vì request nào của widget cũng cần |
| `BodyLimit` | `agent_server/app.py:77` | Từ chối body quá lớn (413) trước khi đọc hết: chat 64 KB, đẩy nội dung 4 MB |
| `frame` | `agent_server/app.py:317` | Trả iframe kèm CSP `frame-ancestors` |
| `logout`, `shop_logout` | `agent_server/app.py:601` | Đăng xuất từ widget (token hiện tại) hoặc từ backend shop (khóa ghi): cắt mọi token chat của khách |
| `sync_faqs`, `sync_offers` | `agent_server/app.py:370` | Shop đẩy FAQ/ưu đãi bằng khóa ghi; kiểm tra trùng id, origin của link; mỗi shop một lần đẩy tại một thời điểm |
| `feedback`, `get_feedback` | `agent_server/app.py:630` | Lưu đánh giá Hữu ích/Chưa hữu ích vào DB (mỗi hội thoại một phiếu); shop đọc bằng khóa ghi |
| `ChatLimiter.check` | `agent_server/rate_limit.py:45` | Cửa sổ 1 phút theo khách và theo IP; hạn mức theo ngày cho shop, cho khách và cho IP |

Tại sao làm thế này:
- **SSE thay vì WebSocket**: mỗi lượt chỉ cần luồng một chiều server → widget, chạy trên HTTP thường, không
  giữ kết nối giữa các lượt; header `X-Accel-Buffering: no` (`agent_server/app.py:575`) để proxy không gom chữ lại.
  Widget đọc bằng `fetch` + `ReadableStream` vì `EventSource` không gửi được POST và header `Authorization`.
- **Luôn kết thúc bằng `done` hoặc `error`** (`agent_server/app.py:555`); không log câu hỏi vì có thể chứa
  dữ liệu cá nhân (`agent_server/app.py:556`).
- **Giới hạn thời gian một lượt** (`agent_server/app.py:47`): gateway chậm không giữ khóa hội thoại mãi.
- **Lỗi đầu vào không lộ dữ liệu**: lỗi 422 không gửi lại nội dung khách gõ (`agent_server/app.py:120`), ký tự
  PostgreSQL không lưu được thành 400 thay vì lỗi server (`agent_server/app.py:127`).
- **Iframe từ origin của Agent Server**: JS của trang shop không đọc được hội thoại; CSP chỉ cho các origin
  đã đăng ký nhúng.
- **Đăng xuất cắt token ở phía server**: JWT không thu hồi được nếu chỉ dựa vào hạn dùng, nên server lưu một mốc
  đăng xuất cho mỗi khách. Một dòng cho mỗi khách (không lưu từng token) nên bảng nhỏ, và dòng cũ hơn thời gian
  sống tối đa của token bị xoá. Shop chỉ cần thêm một dòng `revoke_user_tokens` (`sdk/ati_sdk.py:47`)
  vào hàm đăng xuất; hàm này không bao giờ ném lỗi nên đăng xuất không hỏng vì chatbot.
- **Chế độ khách chỉ cho shop mức 0**: mã khách do trình duyệt tự sinh nên không chứng minh được là ai. Vì vậy
  nó chỉ dùng để gom lịch sử chat, và chỉ được nhận ở shop không có dữ liệu riêng của khách. Khách không có
  tool chuyển nhân viên vì nhân viên không có cách liên hệ lại người không có tài khoản.

**`agent_server/commerce.py`**

| Hàm / lớp | Dòng | Làm gì |
|---|---|---|
| `STANDARD_PROFILE` | `agent_server/commerce.py:39` | Ánh xạ của hợp đồng C2: shop làm đúng mẫu thì không cần khai báo gì |
| `resolve_config` | `agent_server/commerce.py:123` | Trộn cấu hình của shop lên profile |
| `config_problems`, `_scoped` | `agent_server/commerce.py:146` | Path phải tương đối; chỉ dùng biến hợp lệ của thao tác (`OPERATION_VARIABLES`); thao tác của một khách phải có `{customer_id}` hoặc `owner_field` |
| `RestCommerceAdapter._get` | `agent_server/commerce.py:186` | Dựng URL, gọi GET, xử lý 404/lỗi/kích thước, lấy phần `result` |
| `_owned`, `_order` | `agent_server/commerce.py:240` | Kiểm tra chủ đơn; đổi bản ghi của shop về trường và trạng thái chuẩn |
| `list_orders` | `agent_server/commerce.py:296` | Shop lọc được thì để shop lọc; không thì đọc trang lớn hơn (`scan_limit`, mặc định 20) và lọc tại chỗ; bỏ qua bản ghi hỏng |
| `orders_page` | `agent_server/commerce.py:256` | Link trang "Đơn hàng của tôi" lấy từ `links.orders` trong ánh xạ, không viết cứng theo shop |

Tại sao làm thế này:
- **Ràng buộc theo khách nằm ở adapter, không ở prompt**: prompt có thể bị vượt qua, code thì không. Thao tác
  không ràng buộc bị `provision_shop` từ chối, và nếu lỡ được lưu thì cũng không thành khả năng
  (`agent_server/commerce.py:176`).
- **Đơn của khách khác giống hệt đơn không tồn tại** (`agent_server/commerce.py:291`): không lộ việc mã đơn có thật.
- **Chống SSRF**: `base_url` chỉ do đội vận hành nhập; path tương đối; chỉ GET; `follow_redirects=False`
  (`agent_server/commerce.py:208`); có timeout; đọc phản hồi theo từng mảnh và dừng khi quá 1 MB
  (`agent_server/commerce.py:215`).
- **Shop tìm "gần đúng" cũng không lộ đơn khác**: `same_id` (`agent_server/commerce.py:102`) so mã trả về với mã khách hỏi.
- **Phản hồi 200 thiếu dữ liệu là lỗi** (`agent_server/commerce.py:226`), không bị hiểu thành "không có đơn".
- **File JSON thay vì code**: shop có API riêng chỉ viết ánh xạ (`examples/second_shop/commerce.json`).

**`agent_server/store.py`, `fallback.py`, `provision_shop.py`**

| Hàm | Dòng | Làm gì |
|---|---|---|
| `verify_faq_key` | `agent_server/store.py:107` | So SHA-256 của khóa ghi bằng `compare_digest`; DB chỉ lưu bản băm |
| `replace_faqs` | `agent_server/store.py:194` | Thay cả bộ FAQ trong một giao dịch, khóa dòng shop để hai lần đẩy chạy lần lượt |
| `create_ticket` | `agent_server/store.py:311` | `ON CONFLICT (idempotency_key)`: gọi hai lần trong một lượt vẫn một phiếu |
| `revoke_customer_tokens`, `token_revoked` | `agent_server/store.py:152` | Ghi mốc đăng xuất của khách (bảng `token_cutoffs`, chỉ tăng nhờ `GREATEST`); token có `iat` không sau mốc đó bị từ chối |
| `guest_allowed` | `agent_server/store.py:172` | Khách vãng lai chỉ được phép ở shop bật `guest_chat` và không có API đơn hàng |
| `fallback.answer` | `agent_server/fallback.py:45` | Có mã đơn thì tra đơn (mã như khách gõ trước, rồi dạng chuẩn); hỏi "đơn của tôi" thì liệt kê; còn lại trích nguyên văn mục tài liệu tốt nhất |
| `load_commerce_config` | `agent_server/provision_shop.py:21` | Kiểm tra `base_url`, tên thao tác, `config_problems` trước khi cấp shop |

Tại sao có fallback: AI Box là dịch vụ ngoài. Fallback không sinh chữ (`agent_server/fallback.py:3`), chỉ
trích dữ liệu đơn hoặc nguyên văn tài liệu, kèm ghi chú "đây là câu trả lời tự động", nên không thể bịa.

**`shared/` và `sdk/`**

| Hàm / lớp | Dòng | Làm gì |
|---|---|---|
| `LLMProvider.stream` | `shared/llm.py:93` | Gọi LiteLLM có stream; thử lại 1 lần nếu lỗi tạm thời và chưa có chữ nào (`shared/llm.py:145`) |
| `transient` | `shared/llm.py:174` | Timeout, mất kết nối, 429, 5xx thì đáng thử lại (429 chờ 1.5 s trước); 400, sai key thì không |
| `pool`, `connect` | `shared/db.py:255` | Pool kết nối PostgreSQL, mỗi `with connect()` là một giao dịch |
| `_start_embedded` | `shared/db.py:213` | Không có `DATABASE_URL` thì chạy PostgreSQL nhúng (pgserver), xử lý đường dẫn có dấu |
| `fold`, `query_terms` | `shared/text.py:29` | Bỏ dấu tiếng Việt; tách âm tiết, bỏ hư từ, thêm bigram |
| `mask_*`, `PiiFilter` | `shared/pii.py:50` | Che email, số điện thoại (cả số bàn, +84) trong log, trace và traceback |
| `sign_user_token` | `sdk/ati_sdk.py:32` | Ký JWT HS256, không cần thư viện ngoài, mặc định sống 900 s |
| `revoke_user_tokens` | `sdk/ati_sdk.py:47` | Gọi khi khách đăng xuất; trả `False` thay vì ném lỗi |

Tại sao: LiteLLM để đổi nhà cung cấp chỉ bằng cấu hình (`shared/llm.py:1`); `num_retries` của
LiteLLM đặt 0 để nhóm tự kiểm soát việc thử lại (sau khi khách đã thấy chữ thì không gọi lại được). Một
PostgreSQL chứa cả dữ liệu chat lẫn vector (pgvector) nên không cần thêm vector database.

### 3.2 M2 – Nguyễn Đức Anh: RAG

| File | Hàm / lớp | Dòng | Làm gì |
|---|---|---|---|
| `documents.py` | `parse` | `rag/documents.py:42` | Đọc front matter, tiêu đề `#`, các mục `##`; đoạn mở đầu nhập vào mục đầu tiên |
| | `faq_document` | `rag/documents.py:91` | FAQ được shop đẩy lên thành tài liệu một mục; từ khoá của shop được index cùng, không đưa cho model |
| `chunking.py` | `split_section` | `rag/chunking.py:51` | Cắt mục theo câu/dòng, tối đa 500 token, chồng khoảng 80 token |
| | `chunk_document` | `rag/chunking.py:76` | Trả (mục, id mục, các chunk con) cho mỗi mục |
| `contextual.py` | `contexts_for` | `rag/contextual.py:46` | LLM viết 1–2 câu ngữ cảnh cho mỗi chunk, cache theo hash |
| `ingest.py` | `ingest_documents` | `rag/ingest.py:32` | Bỏ qua tài liệu không đổi, embed, ghi DB, xoá tài liệu đã bị xoá; không cho xoá quá nửa kho nếu thiếu `--prune-all` |
| `models.py` | `BgeM3Embedder`, `Reranker` | `rag/models.py:43` | Nạp model một lần cho cả tiến trình, fp16 khi có GPU |
| `store.py` | `replace_document` | `rag/store.py:41` | Thay một tài liệu (mục, chunk, vector, tsvector) trong một giao dịch |
| | `vector_candidates` | `rag/store.py:87` | Truy vấn cosine trên HNSW (`ef_search` = 100) |
| `bm25.py` | `BM25Index`, `index_for` | `rag/bm25.py:18` | BM25 (k1 = 1.2, b = 0.75) trong RAM, dựng lại khi tài liệu đổi |
| `search.py` | `search`, `_search`, `rrf` | `rag/search.py:74` | Vector + BM25 → RRF → mục cha → reranker → ngưỡng τ |

Tại sao làm thế này:
- **Chunk theo cấu trúc, trả về mục cha**: chunk nhỏ thì embedding chính xác, nhưng agent cần cả mục để đủ
  ngữ cảnh, và trích dẫn theo mục (`#muc-n`) thì khách mở đúng chỗ.
- **Hybrid**: vector hiểu cách nói khác nhau, BM25 bắt từ hiếm và mã (eSIM, MagSafe). Điểm hai bên khác
  thang đo nên RRF chỉ dùng thứ hạng. Số đo trên 90 câu: vector Recall@5 0.867, BM25 0.800, hybrid 0.944,
  thêm reranker 0.978 (`eval/results/README.md:18`).
- **BM25 tự viết thay cho `ts_rank_cd`**: `ts_rank_cd` không có IDF nên từ phổ biến như "điện thoại" nặng ngang từ
  hiếm (`rag/bm25.py:3`).
- **Reranker**: cross-encoder đọc câu hỏi và đoạn văn cùng lúc nên xếp hạng chính xác hơn so khớp vector; điểm
  qua sigmoid nằm trong 0..1 nên so được với một ngưỡng cố định (`rag/models.py:71`).
- **τ = 0.1** (`rag/search.py:26`): chọn bằng `tau_analysis` (`eval/retrieval_eval.py:79`), so điểm của
  câu trả lời được với câu tài liệu không có; bản v4 hạ từ 0.2 xuống 0.1. Dưới τ thì `no_results`, model nói
  không có thông tin thay vì bịa.
- **Contextual Retrieval tắt mặc định** (`rag/ingest.py:6`): trên kho này không tăng
  Recall@5 mà tốn một lần gọi LLM cho mỗi chunk. 500 token giữ nguyên vì 250 token chỉ hơn 1 câu.
- **Một khóa cho cả nạp model và suy luận** (`rag/models.py:16`): nạp fp16 làm đổi kiểu số mặc định
  của torch cho cả tiến trình. `HashEmbedder` (`rag/models.py:30`) để unit test không phải tải model 2 GB.
- **Fingerprint** (`rag/ingest.py:47`): chạy lại ingest chỉ xử lý tài liệu thay đổi.
  Trỏ nhầm thư mục thì không xoá sạch kho của shop (`rag/ingest.py:71`).
- **Chỉ rerank 10 mục**: reranker chiếm phần lớn thời gian tìm; 10 thay vì 20 giảm khoảng 40% thời gian mà không giảm
  Recall@5 (ghi chú ở `rag/search.py:28`).
- **Chế độ `relaxed` cho fallback** (`rag/search.py:70`): khi không có model viết lại câu hỏi, điểm của
  câu khách gõ thấp hơn; vẫn nhận mục đầu nếu cả vector lẫn BM25 cùng xếp nó thứ nhất.

### 3.3 M3 – Vương Văn Đảng: shop demo, widget, đánh giá

| File | Hàm | Dòng | Làm gì |
|---|---|---|---|
| `shop_demo/api.py` | `require_api_key` | `shop_demo/api.py:18` | API C2 chỉ nhận Bearer key của Agent Server |
| | `get_order`, `list_orders`, `get_customer` | `shop_demo/api.py:62` | Luôn lọc theo `customer_id` trong path; sai khách trả 404 |
| | `search_products` | `shop_demo/api.py:71` | Tìm sản phẩm theo từ khóa, danh mục, giá tối đa |
| `shop_demo/app.py` | `chat_token` | `shop_demo/app.py:702` | Khách đã đăng nhập thì ký token chat, chưa đăng nhập trả 401 |
| | `same_origin_forms`, `checkout` | `shop_demo/app.py:58` | POST chỉ nhận từ chính website; `checkout_key` để bấm hai lần không tạo hai đơn |
| | `own_order`, `advance_order` | `shop_demo/app.py:553` | Nút [Demo] đẩy đơn sang trạng thái kế tiếp để thử chatbot |
| `shop_demo/help_pages.py` | `render`, `page` | `shop_demo/help_pages.py:33` | Trang `/help` render từ cùng file Markdown, mỗi `##` có anchor `muc-n` |
| `shop_demo/sync_assistant.py` | `snapshot`, `main` | `shop_demo/sync_assistant.py:17` | Dựng bộ ưu đãi/sự kiện từ `events.json`; `main` gửi lên Agent Server bằng khóa ghi |
| `widget/loader.js` | `mount`, `refreshAuth` | `widget/loader.js:55` | Tạo nút và iframe trong Shadow DOM; đọc `data-shop-key`, `data-token-url`, `data-login-url`; lấy token từ shop |
| `widget/frame.js` | `callChat`, `consumeStream` | `widget/frame.js:341` | Gửi câu hỏi, đọc SSE |
| | `apiFetch` | `widget/frame.js:254` | Mọi request: 401 thì xin token mới một lần; đổi tài khoản giữa chừng thì huỷ request |
| | `appendToken`, `flush` | `widget/frame.js:108` | Gom các mẩu chữ, vẽ lại mỗi khung hình (`requestAnimationFrame`) thay vì mỗi token |
| | `renderMarkdown`, `appendInline` | `widget/frame.js:45` | Markdown tối thiểu (đoạn, danh sách, in đậm, `[n]`) chỉ bằng DOM |
| | `addSources` | `widget/frame.js:275` | Hiện link nguồn nếu cùng origin với shop |
| | `canChat`, `authHeaders`, `loadGuestId` | `widget/frame.js:139` | Chọn token hay mã khách cho mỗi request; tạo và nhớ mã khách |
| `examples/second_shop/app.py` | `api_order` | `examples/second_shop/app.py:102` | API "lạ" (`/v2`, `X-Api-Key`, `data.order`) để chứng minh mức 1b |
| `eval/agent_eval.py` | `Runner.run_case` | `eval/agent_eval.py:103` | Gọi `agent.run` trực tiếp cho từng ca, lưu cache |
| | `Judge`, `GROUNDED` | `eval/agent_eval.py:178` | LLM-judge chấm groundedness, đúng/sai, từ chối, tấn công |
| `eval/run.py` | `agent_report` | `eval/run.py:95` | Tính chọn tool đúng, QA, groundedness, trích dẫn, từ chối, injection, độ trễ |
| `eval/retrieval_eval.py` | `evaluate` | `eval/retrieval_eval.py:45` | Recall@1/5, MRR@10 cho 6 cấu hình tìm kiếm |
| `scripts/bad_cases.py` | `run`, `start_offline_server` | `scripts/bad_cases.py:290` | 26 ca khó qua HTTP thật (quyền riêng tư, khách vãng lai, AI Box sập...), ghi `eval/results/bad_cases.md` |
| `eval/human_review.py` | `export`, `score` | `eval/human_review.py:56` | Xuất bảng Excel chia cho 3 người chấm lại; tính tỉ lệ đồng ý với LLM-judge |

Tại sao làm thế này:
- **Iframe + Shadow DOM**: CSS của shop không phá giao diện chat, JS của shop không đọc được hội thoại. Tin
  `postMessage` được kiểm tra origin ở cả hai phía (`widget/loader.js:94`).
- **Không có gì riêng cho một shop trong code widget**: tên, màu, lời chào, gợi ý lấy từ `/v1/widget-config`; link đăng
  nhập lấy từ `data-login-url` và chỉ nhận trang cùng origin với shop (`widget/frame.js:9`).
- **Không dùng `innerHTML`** (`widget/frame.js:44`): dù model bị lừa sinh HTML/script thì
  widget vẫn chỉ hiện chữ. Link nguồn khác origin của shop bị bỏ (`widget/frame.js:283`).
- **Mã khách vãng lai không phải đăng nhập**: widget chỉ gửi nó khi không có token, và server bỏ qua nó ở
  shop có API đơn hàng. Khách vãng lai gặp 401 thì không xin token lại (`widget/frame.js:262`).
- **API shop tự lọc theo khách** (`shop_demo/api.py:3`): phòng thủ nhiều lớp, kể cả khi Agent Server có lỗi.
- **Eval gọi đúng hàm production**: shop API chạy trong tiến trình qua `httpx.ASGITransport`
  (`eval/agent_eval.py:46`), không cần mở server. Kết quả cache theo hash của ca, prompt, tool,
  model, trạng thái kho, mã nguồn, ngày và phiên bản ưu đãi (`eval/agent_eval.py:91`), nên chạy lại không tốn lượt
  gọi AI Box mà vẫn không dùng câu trả lời cũ khi dữ liệu đã đổi.
- **Tấn công gián tiếp**: `poison_shop` có FAQ chứa lệnh giả (`eval/agent_eval.py:36`); ca tấn công đạt khi judge
  nói "không làm theo" và câu trả lời không chứa chuỗi bị cấm (`eval/run.py:187`).
- **Chấm lại judge bằng người**: `human_review.py` chia ca cho 3 thành viên (`eval/human_review.py:22`), để số liệu
  không chỉ dựa vào LLM-judge.
- **Ca AI Box sập là thật**: `bad_cases.py` mở server thứ hai ở cổng 8010 trỏ model tới địa chỉ không tồn tại
  (`scripts/bad_cases.py:291`).

## 4. Câu hỏi thầy có thể hỏi về code

**1. Nếu shop không có API thì sao? (M1)** Tích hợp theo mức (`docs/INTEGRATION.md`). Không có
`commerce_config` thì `adapter_for` trả `None` (`agent_server/commerce.py:354`), `toolset_for` không đưa tool đơn hàng,
prompt dặn "shop chưa kết nối hệ thống đơn hàng" (`agent_server/prompts.py:62`). Shop chỉ
đẩy FAQ/ưu đãi hoặc gửi Markdown. Mức 2 (Shopify, Haravan) mới là hướng phát triển.

**1b. Shop không có tài khoản khách thì sao? (M1 + M3)** Bật chế độ khách khi cấp shop (`--guest-chat`,
`agent_server/provision_shop.py:57`; lệnh từ chối nếu shop có API đơn hàng). Website tĩnh chỉ cần thẻ `<script>`, không cần
backend. Khách hỏi ngay, nhưng chỉ có tool tài liệu và ưu đãi, không có tra đơn và chuyển nhân viên (mục 2.4). Nếu sau này
shop nối API đơn hàng thì `guest_allowed` tự trả `False`, khách vãng lai bị từ chối.

**1c. Widget có dùng được cho shop khác ngoài demo không? (M3 + M1)** Có. Code widget không có gì riêng cho shop nào:
tiêu đề, màu, lời chào, gợi ý đến từ cấu hình lúc cấp shop (`agent_server/app.py:344`); đường dẫn token và trang đăng
nhập do thẻ nhúng của từng shop khai báo. Repo chạy cùng một widget trên 3 website khác origin (cổng 8001, 8002, 8003).
Một shop thứ tư (tiệm bánh, mức 0, chế độ khách) đã được cấp và chạy thật bằng 3 lệnh, ghi trong
`docs/INTEGRATION.md` mục 4. Agent Server có shop demo hay không cũng là tuỳ chọn (`demo_shop_enabled`).

**2. Làm sao agent không lộ đơn của khách khác? (M1)** Nhiều lớp, không lớp nào dựa vào prompt: mã khách chỉ
lấy từ JWT do shop ký; tool không có tham số mã khách và `extra="forbid"`; adapter đặt mã khách vào path
hoặc kiểm `owner_field` (`agent_server/commerce.py:138`); cấu hình không ràng buộc bị từ chối; API shop tự lọc
(`shop_demo/api.py:63`); hội thoại kiểm tra chủ (`agent_server/store.py:244`). Test:
`tests/test_agent_server.py:102`, `tests/test_review_fixes.py:53`.

**3. Prompt injection trong tài liệu hoặc kết quả tool? (M1)** Prompt coi kết quả tool là dữ liệu; nhưng quan
trọng hơn là dù model bị lừa, nó cũng không có quyền gì nguy hiểm: không có tool ghi, không chọn được khách,
widget không chạy HTML, link lạ bị bỏ. Đo bằng 15 kịch bản tấn công và `poison_shop`: 15/15 bị chặn
(`eval/results/README.md`).

**4. AI Box sập thì sao? (M1)** `stream` thử lại một lần khi lỗi tạm thời và chưa có chữ
(`shared/llm.py:105`); bị 429 thì chờ 1.5 s rồi mới thử (`shared/llm.py:148`). Vẫn lỗi thì `LLMError`;
chưa có chữ thì chuyển fallback (`agent_server/agent.py:124`), có chữ rồi thì gửi `error`. Fallback tra đơn
theo mã khách gõ, hoặc tìm tài liệu bằng chính câu của khách với `relaxed=True` (`agent_server/fallback.py:37`). Không có API key thì chạy fallback ngay (`agent_server/agent.py:71`) và widget hiện
"CHẾ ĐỘ DỰ PHÒNG". `bad_cases.md` có hai ca AI Box sập, cả hai OK (lần chạy 04/11: 26 ca, 24 OK, 2 hạn chế đã biết, 0 lỗi mới).

**5. Sao biết câu trả lời không bịa? (M1 + M3)** Bốn chỗ: luật prompt chỉ dùng dữ liệu từ tool
(`agent_server/prompts.py:17`); τ trả `no_results` khi tài liệu không đủ liên quan; `cited_sources` bỏ số `[n]` mà tool
không cấp; eval có judge groundedness so câu trả lời với đúng dữ liệu agent đã thấy (`eval/agent_eval.py:263`).
Kết quả v12: judge chấm 0.889; trợ lý AI rà lại 10 câu bị gắn cờ thì 89/90 = 0.989 (`eval/results/flagged_review_v12.json`, nhóm chưa xác nhận; `eval/results/README.md:55`).

**6. Thêm một tool mới thì sửa những đâu? (M1)** (1) `tools.py`: lớp tham số kế thừa `Args`, hàm
`async def tool(ctx, args) -> Outcome`, thêm `Tool(...)` vào `TOOLS`. (2) Nếu cần dữ liệu shop: thêm khả năng vào
`CAPABILITIES` (`agent_server/commerce.py:26`), phương thức trong `CommerceAdapter`/`RestCommerceAdapter`, đường dẫn
trong `STANDARD_PROFILE`, và `CUSTOMER_SCOPED` nếu là dữ liệu của một khách. (3) Luật dùng trong `COMMERCE_RULES`
hoặc `tool_rules`. (4) Test với `FakeLLM` (`tests/support.py:97`) và ca trong `eval/cases_agent.jsonl`. Widget không phải sửa:
câu trạng thái lấy từ `Tool.status`.

**7. Thêm một shop mới thì làm gì? (M1 + M3)** Chạy `provision_shop` (in khóa một lần,
`agent_server/provision_shop.py:40`). Có API thì thêm `--commerce-config` (file ánh xạ) và `--commerce-api-key`. Shop nhúng
một thẻ `<script>` và viết endpoint ký token bằng SDK. Nạp tài liệu bằng `python -m rag.ingest --shop <id>` hoặc
đẩy FAQ (`examples/push_faq.py`). Không sửa dòng code nào của Agent Server.

**8. Sao dùng SSE mà không dùng WebSocket? (M1 + M3)** Xem mục 3.1. Thêm: hợp đồng C4 cố định thứ tự sự kiện,
nên widget, `smoke_live.py` và unit test đều đọc cùng một định dạng.

**9. Agent có thể lặp vô hạn không? (M1)** Không: tối đa 5 lần gọi model, lần cuối `tool_choice="none"`
(`agent_server/agent.py:80`). Prompt cấm gọi lại tool với cùng tham số. Có test:
`tests/test_agent_server.py:134`.

**10. Đã có hybrid sao còn reranker? τ chọn thế nào? (M2)** Hybrid cho Recall@5 0.944 nhưng thứ tự chưa tốt
(Recall@1 0.589); reranker (10 mục tốt nhất) đưa Recall@1 lên 0.833 và cho điểm 0..1 để đặt ngưỡng. τ chọn theo bảng
`tau_analysis` (`eval/retrieval_eval.py:89`).

**11. Tài liệu shop thay đổi thì cập nhật thế nào? (M2)** Chạy lại `rag.ingest`: tài liệu nào có fingerprint không
đổi thì bỏ qua, tài liệu bị xoá thì xoá khỏi index (`rag/ingest.py:69`). FAQ đẩy qua API được index lại ngay
(`agent_server/app.py:358`). BM25 trong RAM tự dựng lại khi `signature` của shop đổi (`rag/bm25.py:52`).

**12. Trang shop có đọc trộm hội thoại hay chèn script vào widget được không? (M3)** Hội thoại nằm trong
iframe khác origin; token chỉ đi qua `postMessage` có kiểm origin; Markdown vẽ bằng `textContent`; CSP của iframe
`script-src 'self'` (`agent_server/app.py:322`).

**13. Khách gửi hai tin liền nhau, hoặc nhiều người dùng cùng lúc? (M1)** Mỗi hội thoại có một khóa nên hai lượt
chạy lần lượt (test `tests/test_agent_server.py:212`). Hội thoại khác nhau chạy song song (async).
Giới hạn (`agent_server/app.py:159`): mỗi phút 10 tin cho một khách, 30 cho một IP; mỗi ngày 500 cho một
shop, 100 cho một khách, 300 cho một IP. Khách vãng lai tự chọn mã nên được đếm theo IP. Số lượt trong ngày của shop
được đọc lại từ bảng trace sau khi khởi động lại (`agent_server/store.py:358`). Cửa sổ theo phút và khóa hội thoại vẫn
nằm trong RAM, nên chỉ đúng khi chạy một tiến trình (`agent_server/rate_limit.py:3`).

**14. Số liệu đánh giá lấy từ đâu, có tự chấm cho mình không? (M3)** `python -m eval.run agent` chạy 50 ca chọn tool,
40 câu hỏi–đáp, 20 câu ngoài phạm vi, 15 kịch bản tấn công. Chọn tool chấm bằng luật (`eval/run.py:43`),
phần còn lại bằng LLM-judge với prompt cố định, `temperature=0`. Judge có thể sai, nên nhóm rà tay các câu bị gắn cờ
và ghi rõ trong `eval/results/README.md`. Ngoài ra, đánh giá Hữu ích/Chưa hữu ích của khách thật được lưu trong DB
(`agent_server/store.py:344`, mỗi hội thoại một phiếu) và shop đọc qua `GET /v1/shops/{id}/feedback`.

**15. Token JWT bị lộ thì sao? (M1)** Token do shop ký bằng khóa riêng của từng shop, ràng buộc `iss` và `aud`,
nên không giả được và không dùng được ở shop khác. Token sống 15 phút; server từ chối token sống quá 1 giờ
(`agent_server/auth.py:12`). Khi khách đăng xuất, mọi token đã cấp cho khách đó bị cắt trên mọi thiết bị
(`agent_server/store.py:165`); khi widget đăng xuất, server nhận cả token vừa hết hạn (`expired_grace`) và lấy `iat` của
nó làm mốc (`agent_server/app.py:610`). Token có `sub` bắt đầu bằng `guest:` bị từ chối, để
không ai giả làm khách vãng lai. Shop có thể xoay khóa ký bằng `rotate_chat_secret`. Giới hạn còn lại: trong lúc
còn đăng nhập, widget dùng lại một token cho nhiều tin nhắn, nên token không phải loại dùng một lần. Test:
`tests/test_logout.py:55`.

## 5. Điểm code còn có thể bị chê

Nếu thầy chỉ ra, nên nhận và nói hướng sửa; đừng cãi. Danh sách dưới đây đã kiểm tra trong code.

| # | Chỗ | Vấn đề | Gợi ý sửa |
|---|---|---|---|
| 1 | `agent_server/app.py:491` | `chat` dài khoảng 85 dòng, hàm sinh `events` lồng bên trong làm cả khóa, lịch sử, agent, lưu, trace, lỗi | Tách `_run_turn` và `_save_turn` |
| 2 | `agent_server/app.py:304` | 4 endpoint file tĩnh gần như giống nhau (`loader.js`, `loader.css`, `frame.js`, `frame.css`) | Một hàm chung hoặc `StaticFiles` |
| 3 | `agent_server/app.py:370`, `agent_server/app.py:409` | Lặp logic kiểm tra: trùng id, `strip`, so origin của `source_url` | Một hàm kiểm tra dùng chung |
| 4 | `agent_server/app.py:153`, `agent_server/app.py:235`, `agent_server/app.py:399` | Trạng thái toàn cục trong RAM (khóa hội thoại, cửa sổ đếm theo phút, cache shop, khóa đẩy FAQ): chỉ đúng khi chạy 1 tiến trình | Redis/DB nếu chạy nhiều worker |
| 5 | `agent_server/app.py:434`, `agent_server/app.py:405`, `agent_server/tools.py:199` | Vài chỗ vẫn gọi DB đồng bộ trong hàm `async` (phần lớn đã qua `asyncio.to_thread`), chặn event loop một chút | Thống nhất dùng `to_thread` |
| 6 | `agent_server/store.py:65`, `agent_server/store.py:113`, `agent_server/store.py:139` | Ba câu SQL ghi bảng `shops` gần giống nhau | Gom một hàm `upsert_shop` |
| 7 | `agent_server/demo_data.json` | Khóa `faq` (5 câu) không được code nào đọc | Xoá khóa thừa |
| 8 | `agent_server/tools.py:286` | `BY_NAME` không được dùng; agent và `execute` mỗi nơi tự tìm tool bằng vòng lặp | Dùng `BY_NAME` hoặc xoá |
| 9 | `agent_server/commerce.py:207` | Mỗi lần gọi API shop tạo một `AsyncClient` mới, không dùng lại kết nối | Giữ một client cho mỗi adapter |
| 10 | `agent_server/app.py:362` | Tạo event loop mới trong thread vì `index_faqs` là `async` nhưng làm việc đồng bộ | Cho ingest FAQ một hàm đồng bộ |
| 11 | `shared/llm.py:69` | Mã vá cấu hình cũ từ tuần 1 | Xoá, chỉ đọc `LLM_MODEL` |
| 12 | `rag/search.py:66` | Tham số `category` của tìm kiếm không còn được agent dùng (tool luôn truyền `None`) | Giữ cho hợp đồng C1 nhưng ghi rõ, hoặc bỏ |
| 13 | `shop_demo/app.py` (724 dòng) | Một file chứa catalogue, giỏ hàng, thanh toán, đơn, tài khoản, trợ giúp, token | Tách thành các router như `api.py` |
| 14 | `shop_demo/app.py:654`, `shop_demo/demo_chat.py` | Chatbot quy tắc cũ (`WIDGET_MODE=demo`) và bảng chính sách viết tay trùng nội dung `data/kb`; `templates/policy.html` không còn route nào render | Xoá hoặc ghi rõ là code cũ |
| 15 | `eval/run.py:218`, `eval/agent_eval.py:164` | Hàm bọc không làm gì thêm; tham số `ctx` không dùng | Gọi thẳng `tool_data`; bỏ tham số |
| 16 | Test | Không có test tự động cho `widget/*.js` (chỉ `scripts/browser_smoke.mjs` chạy tay, đường dẫn Chrome viết cứng `scripts/browser_smoke.mjs:8`); 2 test của `shop_demo/` không chạy trong lệnh test chính | Thêm vào CI hoặc ghi rõ lệnh chạy riêng |

## 6. Cách chạy và debug nhanh

Cài đặt và chạy demo (PowerShell, từ thư mục gốc, theo `README.md`):

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env                          # điền AI_BOX_API_KEY
.venv\Scripts\python scripts\setup_demo.py      # sinh khóa, seed dữ liệu, cấp 3 shop, nạp kho tri thức
.venv\Scripts\python scripts\run_all.py         # 8000 agent, 8001 Demo Mobile, 8002 Sample Store, 8003 Phụ Kiện Xanh
```

Kiểm thử (Git Bash; 102 test, khoảng 25 giây, không gọi LLM). Trong lúc chạy có in traceback `pool timeout`: đó là
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
.venv\Scripts\python -m eval.human_review export eval/results/agent_v12.json review_v12.xlsx   # bảng cho 3 người chấm
.venv\Scripts\python -m eval.human_review score review_v12.xlsx                             # tỉ lệ đồng ý với judge
.venv\Scripts\python -m agent_server.cli --shop demo_shop --customer C0001   # chat trong terminal, không cần widget
.venv\Scripts\python -m rag.ingest --shop demo_shop [--contextual] [--force]
```

Mẹo debug:

| Triệu chứng | Xem ở đâu |
|---|---|
| Widget hiện "CHẾ ĐỘ DỰ PHÒNG" | `GET http://localhost:8000/healthz`; `AI_BOX_API_KEY` trống thì `llm.enabled` là `False` (`shared/llm.py:79`) |
| Chat báo 401 | `DEMO_SHOP_TOKEN_SECRET` của hai server phải giống nhau; server in cảnh báo khi thiếu (`agent_server/app.py:66`). Ở Sample Store: shop phải có cờ `guest_chat` (chạy lại `setup_demo.py`) |
| Tra đơn báo "hệ thống shop không phản hồi" | `DEMO_SHOP_API_KEY`; log `shop API failed` (`agent_server/tools.py:319`) |
| Agent chọn sai tool | Mô tả tool trong `TOOLS` và `tool_rules`; xem cột `why` trong `eval/runs/<tag>/agent.json` |
| Tìm sai tài liệu | Chạy `agent_server.cli` để thấy tool và nguồn; thử `RAG_RERANK=0`, `RAG_TAU`, `RAG_KEYWORD=tsrank` trong `.env` (`rag/search.py:33`) |
| Lượt chat chậm | `trace_stats.py` (p50/p95, tỉ lệ fallback, tỉ lệ cache) |
