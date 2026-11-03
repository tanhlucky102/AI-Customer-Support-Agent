# Hợp đồng giữa các phần (C1–C6)

| # | Hợp đồng | Cài đặt |
|---|----------|---------|
| C1 | `search(query, shop_id, category=None, k=5) -> list[dict]`; mỗi phần tử có `chunk_id, section_id, content, doc_title, doc, section, url, category, score`. Trả `[]` (tức `no_results`) khi điểm cao nhất dưới τ | `rag/search.py` |
| C2 | REST API đơn hàng của shop: `GET /api/v1/customers/{id}`, `.../orders`, `.../orders/{order_id}` (thêm `GET /api/v1/products` nếu có), header `Authorization: Bearer <key>`, 401/404. Shop dùng API riêng thì khai báo ánh xạ (`docs/INTEGRATION.md`) | `shop_demo/api.py`, `agent_server/commerce.py` |
| C3 | JWT HS256, claims `iss` (shop_id), `sub` (customer_id), `aud="ati-agent"`, `iat`, `exp`, `jti` | `sdk/ati_sdk.py`, `agent_server/auth.py` |
| C4 | `POST /v1/chat` body `{shop_key, message, conversation_id?}`, header `Authorization: Bearer <JWT>`; SSE `status`, `token`, `replace`, `citations`, `done` (hoặc `error`) | `agent_server/app.py`, `widget/frame.js` |
| C5 | `agent.run(ctx, history, message) -> AsyncIterator[event]`; sự kiện `done` chứa `reply, mode, tools[{name,args,ok,output}], sources, usage, llm_calls, first_token_ms` | `agent_server/agent.py` |
| C6 | Bộ eval: `eval/retrieval.jsonl` gồm `id, shop_id, question, expected[{doc, section}]`; `eval/cases_agent.jsonl` gồm `id, type (tool / out_of_scope / attack), shop_id, customer_id, question, history?, expected_tools? / expected_any?, expected_args?, kind?, forbidden?, goal?` | `eval/` |

## Sự kiện SSE (C4)

| Sự kiện | Dữ liệu | Khi nào |
|---------|---------|---------|
| `status` | `{"message": "Đang kiểm tra đơn hàng..."}` | Ngay khi nhận câu hỏi, và trước mỗi lần gọi tool |
| `token` | `{"text": "..."}` | Từng mẩu chữ do mô hình sinh ra (streaming thật) |
| `replace` | `{"text": "..."}` | Hiếm: mô hình viết chữ rồi mới gọi tool (trái luật prompt). Widget thay toàn bộ nội dung bong bóng bằng `text` (câu trả lời tới lúc đó, không có phần chữ thừa) |
| `citations` | `{"sources": [{"ref": 1, "label": "Tài liệu – Mục", "href": "..."}, {"label": "Xem đơn SO10293", "href": "..."}]}` | Sau câu trả lời; nguồn có `ref` ứng với số `[n]` trong câu |
| `done` | `{"conversation_id": "...", "mode": "agent" \| "fallback"}` | Kết thúc lượt |
| `error` | `{"message": "..."}` | Lượt hỏng; luồng không bao giờ bị cắt ngang mà không có sự kiện này |
