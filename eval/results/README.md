# Kết quả đánh giá (30/10/2026)

LLM `ds/deepseek-flash` qua AI Box, bước suy nghĩ ẩn tắt (`LLM_THINKING=0`). Embedding BGE-M3 và reranker
bge-reranker-v2-m3 chạy fp16 trên RTX 4050 Laptop. Mọi lần chạy dùng cùng bộ dữ liệu trong `eval/` và được
chấm bằng cùng một phiên bản LLM-judge. Các phiên bản cũ được chấm lại từ câu trả lời đã lưu, nên điểm so
sánh được với nhau.

## Truy hồi (`ablation/`, 90 câu, không gọi LLM)

| Cấu hình (chỉ mục chunk 500 token, không câu ngữ cảnh) | Recall@1 | Recall@5 | MRR@10 | p50 |
|---|---|---|---|---|
| Vector (BGE-M3) | 0.722 | 0.889 | 0.796 | 42 ms |
| BM25 | 0.489 | 0.733 | 0.599 | 2 ms |
| Postgres `ts_rank_cd` | 0.344 | 0.700 | 0.483 | 24 ms |
| Hybrid BM25 + vector (RRF) | 0.600 | 0.956 | 0.748 | 30 ms |
| Hybrid `ts_rank_cd` + vector | 0.533 | 0.911 | 0.707 | 44 ms |
| **Hybrid + reranker** | **0.800** | **0.967** | **0.873** | 244 ms |

Contextual Retrieval (thêm câu ngữ cảnh do LLM viết) làm Hybrid + reranker giảm từ 0.967 xuống 0.956
Recall@5, tức lệch 1 câu. Chia chunk 250 token cho kết quả giống chunk 500 token, vì các mục tài liệu vốn
đã ngắn.

## Agent: hill-climbing (`agent_v1..v4.json`)

| Phiên bản | Thay đổi | Chọn tool | Hỏi–đáp đúng | Groundedness | Trích dẫn | Từ chối đúng | Injection chặn | p95 tổng | p95 lượt 1 tool |
|---|---|---|---|---|---|---|---|---|---|
| v1 | Prompt ban đầu, τ = 0.2 | 0.98 | 0.900 | 0.843 | 0.983 | 1.00 | 15/15 | 9.0 s | 9.0 s |
| v2 | Gọi tool không viết lời dẫn; cấm thêm lời khuyên ngoài dữ liệu; "eta" là ngày dự kiến; hỏi trước khi chuyển nhân viên; xưng "mình" | 1.00 | 0.875 | 0.889 | 1.000 | 1.00 | 15/15 | 5.8 s | 7.0 s |
| v3 | Bỏ tham số `category` của tool tìm kiếm; giữ từ khoá của khách trong truy vấn; câu hỏi chính sách chung trả lời ngay | 1.00 | 0.925 | 0.889 | 1.000 | 1.00 | 15/15 | 5.3 s | 6.7 s |
| **v4** | τ = 0.1, hiệu chỉnh trên truy vấn do agent viết | **1.00** | **0.950** | **0.922** | **1.000** | **1.00** | **15/15** | **4.5 s** | **4.7 s** |

Số ca: 50 ca chọn tool, 40 câu hỏi–đáp, 90 câu trả lời được chấm groundedness, 20 câu ngoài phạm vi, 15
kịch bản prompt injection.

## Baseline (`baseline_kb_in_context.json`, 20 câu của Demo Mobile)

| | Hỏi–đáp đúng | Token vào/câu | p50 | p95 |
|---|---|---|---|---|
| Nhồi cả kho tri thức vào prompt (68 nghìn token, 99.7% được cache) | 1.00 | 68,418 | 2.3 s | 3.9 s |
| RAG agent (v4) | 0.95 | 5,950 | 3.4 s | 4.5 s |
