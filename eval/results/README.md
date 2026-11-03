# Kết quả đánh giá (30/10 và 03/11/2026)

LLM `ds/deepseek-flash` qua AI Box, bước suy nghĩ ẩn tắt (`LLM_THINKING=0`). Embedding BGE-M3 và reranker
bge-reranker-v2-m3 chạy fp16 trên RTX 4050 Laptop. Mọi lần chạy dùng cùng bộ dữ liệu trong `eval/` và được
chấm bằng cùng một phiên bản LLM-judge. Các phiên bản cũ được chấm lại từ câu trả lời đã lưu, nên điểm so
sánh được với nhau.

## Truy hồi (`ablation/`, 90 câu, không gọi LLM)

| Cấu hình (chỉ mục chunk 500 token, không câu ngữ cảnh) | Recall@1 | Recall@5 | MRR@10 | p50 |
|---|---|---|---|---|
| Vector (BGE-M3) | 0.678 | 0.867 | 0.758 | 30 ms |
| BM25 | 0.489 | 0.800 | 0.609 | 3 ms |
| Postgres `ts_rank_cd` | 0.322 | 0.711 | 0.484 | 54 ms |
| Hybrid BM25 + vector (RRF) | 0.589 | 0.944 | 0.734 | 32 ms |
| Hybrid `ts_rank_cd` + vector | 0.533 | 0.922 | 0.689 | 83 ms |
| **Hybrid + reranker** | **0.811** | **0.967** | **0.877** | 232 ms |

Đo lại ngày 03/11/2026 sau khi sửa các lỗi tìm được ở vòng review cuối (stopword có dấu, đoạn mở đầu tài
liệu, không còn chunk trùng). Contextual Retrieval (thêm câu ngữ cảnh do LLM viết) giữ nguyên Recall@5 của
Hybrid + reranker (0.967) nhưng MRR thấp hơn (0.864 so với 0.877) và vector-only giảm 0.867 → 0.844. Chunk
250 token tìm thêm 1 câu (Recall@5 0.978), nằm trong sai số, nên giữ 500 token.

## Agent: hill-climbing (`agent_v1..v9.json`)

| Phiên bản | Thay đổi | Chọn tool | Hỏi–đáp đúng | Groundedness | Trích dẫn | Từ chối đúng | Injection chặn | p95 tổng | p95 lượt 1 tool |
|---|---|---|---|---|---|---|---|---|---|
| v1 | Prompt ban đầu, τ = 0.2 | 0.98 | 0.900 | 0.843 | 0.983 | 1.00 | 15/15 | 9.0 s | 9.6 s |
| v2 | Gọi tool không viết lời dẫn; cấm thêm lời khuyên ngoài dữ liệu; "eta" là ngày dự kiến; hỏi trước khi chuyển nhân viên; xưng "mình" | 1.00 | 0.875 | 0.889 | 1.000 | 1.00 | 15/15 | 5.8 s | 8.0 s |
| v3 | Bỏ tham số `category` của tool tìm kiếm; giữ từ khoá của khách trong truy vấn; câu hỏi chính sách chung trả lời ngay | 1.00 | 0.925 | 0.889 | 1.000 | 1.00 | 15/15 | 5.3 s | 6.7 s |
| v4 | τ = 0.1, hiệu chỉnh trên truy vấn do agent viết | 1.00 | 0.950 | 0.922 | 1.000 | 1.00 | 15/15 | 4.5 s | 4.7 s |
| v5 | Sửa các lỗi của vòng review cuối; thử lại 1 lần khi AI Box treo; mẫu hỏi–đáp trải đều 80 câu Demo Mobile; chạy tuần tự | 1.00 | 0.900 | 0.900 | 1.000 | 1.00 | 15/15 | 9.3 s | 9.3 s |
| v6 | Bỏ lời dẫn viết trước khi gọi tool; câu hỏi "làm thế nào" tra tài liệu trước khi hỏi mã đơn; câu hỏi cách dùng sản phẩm là đúng chủ đề | 1.00 | 0.925 | 0.856 | 1.000 | 1.00 | 15/15 | 33.2 s | 32.9 s |
| v7 | Ngày khuyến mãi theo giờ Việt Nam (trước đó là UTC); tìm lại bằng từ khác khi không có kết quả; thời hạn tính từ ngày giao dự kiến | 1.00 | 0.925 | 0.843 | 1.000 | 1.00 | 15/15 | 30.9 s | 30.9 s |
| v8 | Code nhận ra câu hỏi tiếng Anh và yêu cầu trả lời tiếng Anh | 1.00 | 0.950 | 0.911 | 1.000 | 1.00 | 15/15 | 15.8 s | 19.0 s |
| **v9** | Từ khoá tìm sản phẩm viết theo tên trong catalogue (tiếng Việt) | **1.00** | **1.000** | **0.900** | **1.000** | **1.00** | **15/15** | **4.8 s** | **4.3 s** |

Số ca: 50 ca chọn tool, 40 câu hỏi–đáp, 90 câu trả lời được chấm groundedness, 20 câu ngoài phạm vi, 15
kịch bản prompt injection. Percentile tính theo nearest-rank; độ trễ v1–v4 được tính lại từ câu trả lời đã
lưu (`latency_recomputed_v1_v4.json`).

- v1–v4 chạy ngày 30/10 với 3 phiên đồng thời và mẫu hỏi–đáp cũ (nghiêng về chính sách); v5–v9 chạy ngày
  03/11, từng phiên một, với mẫu mới. Điểm hỏi–đáp chỉ so được trong từng nhóm.
- Độ trễ v5–v8 phản ánh AI Box tối 03/11 (chậm, treo 30 giây ở 2–8/125 lượt), không phải thay đổi của hệ
  thống. Khi chạy v9, AI Box đã ổn định lại (1 lượt treo, được thử lại thành công).
- Groundedness của v9 sau khi rà tay 9 câu bị gắn cờ: 89/90 = 0.989. 8 câu là judge chấm sai (tính số ngày
  trễ đúng, suy luận bảo hành đúng, URL shop có trong system prompt mà judge không thấy, phép cộng giá đúng);
  câu còn lại là lời hứa "phản hồi trong 24 giờ" mặc định của framework khi chuyển nhân viên.

## Baseline (`baseline_kb_in_context.json`, 20 câu của Demo Mobile)

| | Hỏi–đáp đúng | Token vào/câu | p50 | p95 |
|---|---|---|---|---|
| Nhồi cả kho tri thức vào prompt (73 nghìn token, 99.7% được cache) | 0.95 | 73,048 | 2.0 s | 2.8 s |
| RAG agent (v9) | 1.00 | 6,295 | 3.7 s | 4.9 s |

Chạy ngày 03/11 sau v9, cả hai bên tuần tự. Thí nghiệm bật/tắt bước suy nghĩ ẩn (`agent_v4_thinking.json`)
được chạy trên cấu hình v4 ngày 30/10 và không chạy lại.
