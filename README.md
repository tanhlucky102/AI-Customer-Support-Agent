# AI Customer Support Agent

Trợ lý chăm sóc khách hàng **nhúng vào website thương mại điện tử bằng một thẻ `<script>`**. Bên trong là một agent
(Agentic RAG) tự quyết định tra tài liệu của shop, tra đơn hàng, hỏi lại khách hay chuyển cho nhân viên.

- **LLM:** `ds/deepseek-flash` qua AI Box, gọi bằng LiteLLM; có thể cấu hình model dự phòng.
- **RAG:** BGE-M3 + BM25, gộp bằng RRF, rerank bằng `bge-reranker-v2-m3`, trả lời kèm trích dẫn; PostgreSQL + pgvector.
- **Tích hợp theo mức:** shop không có API vẫn dùng được (chỉ FAQ, khách không cần đăng nhập); shop có API riêng
  nối bằng một file JSON, không viết code.

```
Website shop ──<script loader.js>──> Widget (iframe) ──SSE──> Agent Server (FastAPI)
                                                              ├─ Agent: LLMProvider (LiteLLM → AI Box) + 7 tool
                                                              ├─ RAG: BGE-M3 + BM25 → RRF → reranker (PostgreSQL + pgvector)
                                                              └─ CommerceAdapter ──REST──> API đơn hàng của từng shop
```

## 1. Cài đặt

Yêu cầu: **Python 3.11**. Nên có GPU NVIDIA (chỉ CPU thì chậm hơn). Node.js 21+ và Google Chrome chỉ cần cho test
JavaScript, test trình duyệt và quay video. Không cần cài PostgreSQL hay Docker.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          # mở .env, điền AI_BOX_API_KEY
```

- Khi `DATABASE_URL` trong `.env` để trống, server tự chạy PostgreSQL nhúng kèm pgvector.
- Lần chạy đầu tải BGE-M3 và reranker (khoảng 4,5 GB).

## 2. Chạy demo

```powershell
.venv\Scripts\python scripts\setup_demo.py     # sinh khóa vào .env, seed dữ liệu, cấp 3 shop, nạp kho tri thức
.venv\Scripts\python scripts\run_all.py        # bật 4 server; Ctrl+C để dừng
```

`setup_demo.py` chạy lại bao nhiêu lần cũng được.

| Trang | Tài khoản | Mức tích hợp |
|-------|-----------|--------------|
| http://localhost:8001 (Demo Mobile) | `an@demo.vn` / `demo1234` (có đơn SO10293) | 1: API theo chuẩn của dự án |
| http://localhost:8003 (Phụ Kiện Xanh) | `chau@pkx.vn` / `demo1234` (có đơn PKX204518) | 1: API riêng, nối bằng `commerce.json` |
| http://localhost:8002 (Sample Store) | không cần, khách vãng lai | 0: chỉ có FAQ, không API |

Agent Server chạy ở http://localhost:8000 (`/healthz` cho biết đang dùng model hay chế độ dự phòng).

Câu hỏi thử: *"Phí giao hàng cho đơn 300 nghìn là bao nhiêu?"*, *"Đơn SO10293 của tôi tới đâu rồi?"*,
*"Đơn đó trễ vậy thì shop có hỗ trợ gì không?"*, *"Is the iPhone 16 in stock?"*.

Chat trong terminal, không cần widget:

```powershell
.venv\Scripts\python -m agent_server.cli --shop demo_shop --customer C0001
```

## 3. Kiểm thử

```powershell
.venv\Scripts\python -m unittest discover -s tests -t .     # 116 test, không gọi LLM, khoảng 1 phút
```

- Lệnh này chạy luôn test của `shop_demo/` và test JavaScript của widget (`node --test`, bỏ qua nếu máy không có Node).
- Trong lúc chạy có in traceback `pool timeout` hoặc `db down`: đó là test cố tình giả lỗi; kết quả cuối phải là `OK`.

Khi `run_all.py` đang chạy (cần `AI_BOX_API_KEY`):

```powershell
.venv\Scripts\python scripts\smoke_live.py      # đăng nhập, chính sách, tra đơn, chặn khách khác, shop thứ hai
.venv\Scripts\python scripts\bad_cases.py       # 26 tình huống khó qua server thật -> eval\results\bad_cases.json
node scripts\browser_smoke.mjs                  # mở widget bằng Chrome headless và hỏi thử
node scripts\record_demo.mjs demo.mp4           # quay video demo có chú thích (cần Chrome và ffmpeg)
```

## 4. Đánh giá

```powershell
.venv\Scripts\python -m eval.run retrieval                  # Recall@k, MRR (không gọi LLM)
.venv\Scripts\python -m eval.run agent --tag v13            # chọn tool, trả lời đúng, groundedness, từ chối, tấn công
.venv\Scripts\python -m eval.run baseline                   # nhồi cả kho tri thức vào prompt so với RAG
.venv\Scripts\python -m eval.run agent --dataset eval\heldout --tag heldout   # bộ giữ lại: chỉ chạy MỘT lần
.venv\Scripts\python -m eval.human_review export eval\results\agent_v13.json review.xlsx   # bảng để chấm tay
.venv\Scripts\python -m eval.human_review score review.xlsx                                # tỉ lệ người và judge đồng ý
```

Kết quả mỗi lần chạy nằm ở `eval/runs/<tag>/` (không commit); kết quả đã công bố nằm ở `eval/results/`.

## 5. Thêm một shop mới

```powershell
# Mức 0: chỉ có nội dung, khách không cần đăng nhập
.venv\Scripts\python -m agent_server.provision_shop --shop-id tiem_banh --shop-url https://tiembanh.vn --title "Trợ lý Tiệm Bánh" --guest-chat
# Mức 1: có API đơn hàng riêng, nối bằng file ánh xạ (ví dụ: examples\second_shop\commerce.json)
.venv\Scripts\python -m agent_server.provision_shop --shop-id my_shop --shop-url https://myshop.vn --title "Trợ lý My Shop" --commerce-config commerce.json --commerce-api-key <key>
```

Lệnh in ra `public_key`, `faq_write_key` và `chat_token_secret` **một lần**. Sau đó:

1. **Nội dung:** đẩy FAQ bằng khóa ghi (`examples\push_faq.py`, `examples\push_offers.py`), hoặc đặt tài liệu
   Markdown vào `data\kb\<shop_id>\` rồi chạy `python -m rag.ingest --shop <shop_id>`.
2. **Nhúng widget** vào trang:

   ```html
   <script src="https://<agent-server>/widget/loader.js" data-shop-key="pk_..."
           data-token-url="/api/chat-token" data-login-url="/dang-nhap"></script>
   ```

   Shop mức 0 dùng chế độ khách thì chỉ cần `data-shop-key`.
3. **Shop có tài khoản khách:** backend shop cấp token chat cho khách đang đăng nhập và thu hồi khi khách đăng xuất,
   bằng SDK `sdk\ati_sdk.py` (không cần thư viện ngoài):

   ```python
   from ati_sdk import sign_user_token, revoke_user_tokens
   token = sign_user_token(user.id, shop_id=SHOP_ID, secret=CHAT_TOKEN_SECRET)              # GET /api/chat-token
   revoke_user_tokens(user.id, shop_id=SHOP_ID, write_key=WRITE_KEY, server_url=AGENT_URL)  # khi đăng xuất
   ```

Agent chỉ được cấp những tool ứng với khả năng thật của shop: shop không có API đơn hàng thì không có tool tra đơn.

## 6. Cấu hình chính (`.env`)

| Biến | Ý nghĩa |
|------|---------|
| `AI_BOX_API_KEY`, `LLM_MODEL` | Khóa và model chính (mặc định `openai/ds/deepseek-flash`) |
| `LLM_FALLBACK_MODEL`, `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_API_KEY` | Model dự phòng khi AI Box lỗi, ví dụ `ollama_chat/qwen2.5:7b` và `http://localhost:11434`; để trống là không có |
| `DATABASE_URL` | PostgreSQL có pgvector; để trống thì dùng PostgreSQL nhúng |
| `RATE_LIMIT_*`, `DAILY_CHAT_QUOTA_PER_SHOP` | Giới hạn số tin mỗi phút, mỗi ngày |
| `RATE_LIMIT_BACKEND` | `db` (mặc định, đúng khi chạy nhiều tiến trình) hoặc `memory` |
| `RAG_RERANK`, `RAG_TAU` | Bật reranker; ngưỡng τ để từ chối khi tài liệu không đủ liên quan |

Agent Server chạy được nhiều tiến trình (`uvicorn agent_server.app:app --workers 4`): khóa hội thoại, giới hạn tần
suất và khóa đẩy FAQ đều nằm trong PostgreSQL.

## 7. Docker (tuỳ chọn)

```bash
cp .env.example .env                                         # điền AI_BOX_API_KEY
docker compose run --rm agent python scripts/setup_demo.py
docker compose up --build
```

CI (`.github/workflows/ci.yml`) chạy lint, toàn bộ test và dựng thử Docker mỗi lần push.

## 8. Cấu trúc thư mục

| Thư mục | Nội dung |
|---------|----------|
| `agent_server/` | FastAPI: widget, API chat (SSE), agent (`agent.py`, `tools.py`, `prompts.py`), `commerce.py`, xác thực, giới hạn tần suất, cấp shop |
| `rag/` | Đọc tài liệu, chia chunk, embedding, BM25, tìm kiếm lai, nạp kho (`python -m rag.ingest`) |
| `shared/` | PostgreSQL + pgvector, `LLMProvider` (có failover), xử lý tiếng Việt, che dữ liệu cá nhân |
| `widget/` | `loader.js` chạy trên trang shop; giao diện chat trong iframe (`frame.*`, `frame-core.js`) |
| `shop_demo/` | Website Demo Mobile (FastAPI + SQLite): trang, giỏ hàng, đơn hàng, API cho agent |
| `examples/` | Shop thứ hai có API riêng + `commerce.json`; trang tĩnh Sample Store; script đẩy FAQ/ưu đãi |
| `sdk/` | `sign_user_token`, `revoke_user_tokens` cho backend shop |
| `data/kb/` | Kho tri thức Markdown của 2 shop (dữ liệu, không phải tài liệu dự án) |
| `eval/` | Bộ đánh giá (`retrieval.jsonl`, `cases_agent.jsonl`, `heldout/`), harness, kết quả |
| `tests/` | Unit test và test hợp đồng (Python), `tests/js/` (widget) |
| `scripts/` | `setup_demo`, `run_all`, `smoke_live`, `bad_cases`, `browser_smoke.mjs`, `record_demo.mjs`, `trace_stats` |
