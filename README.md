# AI Customer Support Agent

Framework chatbot chăm sóc khách hàng **nhúng vào website thương mại điện tử bằng một thẻ `<script>`**. Lõi
là một agent dùng **Agentic RAG**: mô hình tự quyết định tra tài liệu của shop, tra đơn hàng, hỏi lại khách
hay chuyển cho nhân viên. Agent chạy trong vòng lặp Reason → Act → Observe, gọi tool qua function calling.

- **LLM:** `ds/deepseek-flash` qua AI Box (chuẩn OpenAI), gọi bằng LiteLLM. Nếu AI Box lỗi, hệ thống chuyển
  sang trả lời theo quy tắc.
- **RAG:** tài liệu Markdown được chia chunk theo cấu trúc. Mỗi chunk được thêm câu ngữ cảnh do LLM sinh
  (Contextual Retrieval) rồi embed bằng BGE-M3 lưu trong pgvector (HNSW). Tìm kiếm kết hợp vector và BM25,
  gộp bằng RRF, rerank bằng `bge-reranker-v2-m3`, trả về đoạn cha kèm trích dẫn, và trả `no_results` khi
  điểm thấp hơn ngưỡng τ.
- **Tích hợp shop theo mức:**
  - mức 0: shop chỉ có nội dung;
  - mức 1: shop có API theo hợp đồng C2, hoặc API riêng được ánh xạ bằng một file JSON, không phải viết
    code.

  Agent chỉ thấy tool ứng với khả năng thật của shop. Xem [docs/INTEGRATION.md](docs/INTEGRATION.md).
- **Bảo mật:**
  - đăng nhập bắt buộc (JWT do shop ký);
  - `customer_id` chỉ lấy từ token;
  - widget chạy trong iframe với CSP `frame-ancestors`;
  - kiểm tra Origin, giới hạn tần suất gọi;
  - che PII trong log và trace;
  - kết quả tool được coi là dữ liệu, không phải lệnh.

```
Website shop ──<script loader.js>──> Widget (iframe) ──SSE──> Agent Server (FastAPI)
                                                              ├─ Agent core: LLMProvider (LiteLLM → AI Box) + 7 tools
                                                              ├─ RAG: BGE-M3 + BM25 → RRF → reranker   (PostgreSQL + pgvector)
                                                              └─ CommerceAdapter ──REST──> API đơn hàng của từng shop
```

## Chạy nhanh trên Windows (không cần Docker)

Yêu cầu: Python 3.11. Nên có GPU NVIDIA; nếu chỉ dùng CPU thì chạy chậm hơn.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # điền AI_BOX_API_KEY
.venv\Scripts\python scripts\setup_demo.py     # sinh khóa, seed dữ liệu, cấp 3 shop, nạp kho tri thức
.venv\Scripts\python scripts\run_all.py        # 4 server: 8000 agent, 8001, 8002, 8003
```

- Database: khi `DATABASE_URL` để trống, server tự chạy PostgreSQL nhúng kèm pgvector (gói `pgserver`).
  Không cần cài Postgres hay Docker.
- Model: lần chạy đầu tải BGE-M3 và reranker (khoảng 4.5 GB).

| Trang | Tài khoản | Mức tích hợp |
|-------|-----------|--------------|
| http://localhost:8001 — Demo Mobile | `an@demo.vn` / `demo1234` (có đơn SO10293) | 1, API theo hợp đồng C2 |
| http://localhost:8003 — Phụ Kiện Xanh | `chau@pkx.vn` / `demo1234` (có đơn PKX204518) | 1, API riêng + `commerce.json` |
| http://localhost:8002 — Sample Store | (trang tĩnh, chỉ có FAQ) | 0 |

Chat trong terminal, không cần widget:

```powershell
.venv\Scripts\python -m agent_server.cli --shop demo_shop --customer C0001
```

## Docker Compose

```bash
docker compose up --build
docker compose run --rm agent python scripts/setup_demo.py
```

## Cấu trúc thư mục

| Thư mục | Nội dung |
|---------|----------|
| `agent_server/` | FastAPI: widget, API chat SSE, agent core (`agent.py`, `tools.py`, `prompts.py`), `commerce.py`, đăng ký shop, rule fallback |
| `rag/` | Đọc tài liệu, chia chunk, Contextual Retrieval, embedding, BM25, tìm kiếm lai, nạp kho (`python -m rag.ingest`) |
| `shared/` | Kết nối PostgreSQL + pgvector, `LLMProvider`, xử lý tiếng Việt, che PII |
| `widget/` | `loader.js` và giao diện chat trong iframe (Markdown đã lọc, trích dẫn) |
| `shop_demo/` | Website Demo Mobile: đăng nhập, đơn hàng, REST API C2, trang trợ giúp từ kho tri thức |
| `examples/second_shop/` | Website thứ hai có API riêng, kèm file ánh xạ `commerce.json` |
| `data/kb/` | Kho tri thức: 44 tài liệu của Demo Mobile, 8 tài liệu của Phụ Kiện Xanh |
| `sdk/` | `sign_user_token` cho backend shop |
| `eval/` | Bộ đánh giá: 90 câu truy hồi, 50 ca chọn tool, 20 câu ngoài phạm vi, 15 kịch bản tấn công; harness và kết quả |
| `tests/` | Unit test và test hợp đồng (chạy với embedder giả, không gọi LLM) |
| `docs/` | Hướng dẫn tích hợp, các hợp đồng C1–C6 |

## Kiểm thử và đánh giá

```powershell
.venv\Scripts\python -m unittest discover -s tests -t .            # test hợp đồng, bảo mật, RAG
.venv\Scripts\python -m eval.run retrieval                          # Recall@k, MRR theo từng cấu hình
.venv\Scripts\python -m eval.run agent                              # chọn tool, groundedness, từ chối, tấn công
.venv\Scripts\python -m eval.run baseline                           # nhồi cả kho vào prompt vs RAG
.venv\Scripts\python scripts\smoke_live.py                          # khi run_all.py đang chạy
```

Kết quả đã chạy nằm trong `eval/results/` (xem `eval/results/README.md`). Cấu hình cuối cùng:

| Chỉ số | Mục tiêu | Kết quả |
|--------|----------|---------|
| Recall@5 / MRR@10 (90 câu) | ≥ 0.85 / ≥ 0.75 | 0.967 / 0.877 |
| Chọn đúng tool (50 ca) | ≥ 0.90 | 1.00 |
| Trả lời đúng (40 câu, LLM judge) | — | 1.00 |
| Groundedness (90 câu trả lời) | ≥ 0.95 | 0.900 (0.989 sau khi rà tay) |
| Có trích dẫn / từ chối đúng | ≥ 0.95 / ≥ 0.90 | 1.00 / 1.00 |
| Tấn công bị chặn (15 injection + 5 xác thực) | 20/20 | 20/20 |
| p95 câu trả lời có 1 tool | ≤ 6 s | 4.3 s (phụ thuộc tải của AI Box) |
