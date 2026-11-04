# Kết quả đánh giá (30/10, 03/11 và 04/11/2026)

LLM `ds/deepseek-flash` qua AI Box, bước suy nghĩ ẩn tắt (`LLM_THINKING=0`). Embedding BGE-M3 và reranker
bge-reranker-v2-m3 chạy fp16 trên RTX 4050 Laptop. Mọi lần chạy dùng cùng bộ dữ liệu trong `eval/` và được
chấm bằng cùng một phiên bản LLM-judge. Các phiên bản cũ được chấm lại từ câu trả lời đã lưu, nên điểm so
sánh được với nhau.

## Truy hồi (`retrieval_v10.json`, 90 câu, không gọi LLM, đo ngày 04/11)

| Cấu hình (chỉ mục chunk 500 token, không câu ngữ cảnh) | Recall@1 | Recall@5 | MRR@10 | Recall@5 có cổng τ | p50 |
|---|---|---|---|---|---|
| Vector (BGE-M3) | 0.678 | 0.867 | 0.758 | 0.844 | 42 ms |
| BM25 | 0.489 | 0.800 | 0.609 | — | 4 ms |
| Postgres `ts_rank_cd` | 0.322 | 0.711 | 0.484 | — | 71 ms |
| Hybrid BM25 + vector (RRF) | 0.589 | 0.944 | 0.734 | 0.900 | 44 ms |
| Hybrid `ts_rank_cd` + vector | 0.533 | 0.922 | 0.689 | 0.867 | 108 ms |
| Hybrid + rerank 20 mục (cấu hình trước 04/11) | 0.811 | 0.967 | 0.877 | 0.700 | 294 ms |
| **Hybrid + rerank 10 mục** (cấu hình cuối) | **0.833** | **0.978** | **0.889** | **0.700** | 171 ms |

- Rerank 10 mục thay vì 20 giảm khoảng 40% thời gian tìm kiếm và không giảm recall.
- "Recall@5 có cổng τ" là kết quả khi tìm bằng nguyên văn câu của khách (đường dự phòng khi không có model).
  Agent tự viết lại câu truy vấn nên không gặp cổng này (`tau_agent_queries_v12.json`: τ = 0.1 giữ 100% câu
  trả lời được, chặn 75% câu tài liệu không có). Ở đường dự phòng, nhận thêm mục đứng đầu khi vector và BM25
  cùng xếp nó thứ nhất: tìm đúng 68/90 thay vì 63/90, không nhận nhầm thêm câu nào (`fallback_retrieval_v10.json`).
- Biến thể chỉ mục (`ablation/`, đo 03/11 với rerank 20 mục): Contextual Retrieval giữ nguyên Recall@5 (0.967)
  nhưng MRR thấp hơn (0.864 so với 0.877); chunk 250 token tìm thêm 1 câu, nằm trong sai số, nên giữ 500 token.

## Agent: hill-climbing (`agent_v1..v13.json`)

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
| v9 | Từ khoá tìm sản phẩm viết theo tên trong catalogue (tiếng Việt) | 1.00 | 1.000 | 0.900 | 1.000 | 1.00 | 15/15 | 4.8 s | 4.3 s |
| v10 | Sửa các lỗi của vòng review thứ hai: rerank 10 mục, câu hứa chuyển nhân viên do shop cấu hình, "tài liệu không đề cập" thay vì "shop không có", khách vãng lai ở Sample Store, sửa nhãn 2 câu | 1.00 | 0.975 | 0.878 | 1.000 | 1.00 | 15/15 | 3.6 s | 3.3 s |
| v11 | Bảng từ viết tắt khi chat ("ck" = chuyển khoản); judge được báo đúng câu hứa của shop | 1.00 | 1.000 | 0.889 | 1.000 | 1.00 | 15/15 | 3.8 s | 3.5 s |
| v12 | Nói rõ đơn chưa giao đã trễ bao nhiêu ngày; hiển thị giá trị bị che mà không tự giải thích | 1.00 | 1.000 | 0.889 | 1.000 | 1.00 | 15/15 | 3.7 s | 3.5 s |
| **v13** | Ngày giao thật `delivered_at` trong hợp đồng C2; số ngày (trễ, đã nhận hàng) do code tính và đưa vào kết quả tool; failover sang model thứ hai; khóa và giới hạn trong PostgreSQL (không đổi cách agent trả lời) | **1.00** | **0.975** | **0.966** | **1.000** | **1.00** | **15/15** | **30.6 s** | **29.1 s** |

Số ca: 50 ca chọn tool, 40 câu hỏi–đáp, 90 câu trả lời được chấm groundedness, 20 câu ngoài phạm vi, 15
kịch bản prompt injection. Percentile tính theo nearest-rank; độ trễ v1–v4 được tính lại từ câu trả lời đã
lưu (`latency_recomputed_v1_v4.json`).

- v1–v4 chạy ngày 30/10 với 3 phiên đồng thời và mẫu hỏi–đáp cũ (nghiêng về chính sách); v5–v9 chạy ngày
  03/11, v10–v12 ngày 04/11, từng phiên một, với mẫu mới. Từ v10 có thêm nhãn đã sửa (o-14, o-19) và 3 ca
  Sample Store chạy như khách vãng lai. Điểm hỏi–đáp chỉ so được trong từng nhóm.
- Độ trễ v5–v8 phản ánh AI Box tối 03/11 (chậm, treo 30 giây ở 2–8/125 lượt), không phải thay đổi của hệ
  thống. Khi chạy v9, AI Box đã ổn định lại (1 lượt treo, được thử lại thành công).
- v13 (04/11, khoảng 09:40–10:00): hỏi–đáp 39/40, câu sai duy nhất là lượt AI Box treo giữa câu trả lời
  (`q-r-gd-044`, kết thúc bằng sự kiện `error`). Judge gắn cờ 3/89 câu (v12: 10/90) vì số ngày giờ nằm sẵn trong
  kết quả tool. **Độ trễ v13 là của AI Box lúc đó, không phải của code:** đường gọi model không đổi, và ngay sau lần
  chạy, 10 lời gọi chỉ yêu cầu trả lời một chữ mất p50 3,7 s, 2/10 lời gọi treo khoảng 30 s
  (`aibox_probe_v13.json`). Bỏ 14 lượt treo trên 15 s thì p95 còn 11,3 s.
- Sửa harness ngày 04/11: một lượt kết thúc bằng lỗi (không có câu trả lời) trước đây vẫn được gửi cho judge, và judge
  đã chấm câu rỗng là "đúng" dựa vào tài liệu tham chiếu. Giờ lượt lỗi luôn tính là sai và không gửi cho judge. Chỉ v13
  và bộ held-out có lượt như vậy (v1–v12 không có), nên các số cũ không đổi.
- Các câu judge gắn cờ "không có căn cứ" được trợ lý AI (Claude Code) rà lại từng câu, đối chiếu với dữ liệu
  bot thực sự nhận được; kết quả lưu ở `flagged_review_v10.json` … `_v13.json` và **chưa được nhóm
  xác nhận**. v13: 2/3 câu đúng (suy luận còn hạn bảo hành; "bạn chưa đăng nhập" có trong system prompt của khách
  vãng lai, judge không thấy system prompt), 1 câu thật sự thêm lời khuyên ghép sạc với cáp → 88/89 = 0.989. v12: 9/10 câu đúng (6 câu tính số ngày trễ, ngày giao dự kiến, suy luận bảo hành, một từ gõ sai
  "phụ cập"), 1 câu thật sự thêm lời khuyên không có trong dữ liệu → 89/90 = 0.989. Chỉ câu bị gắn cờ được rà,
  nên con số này chỉ có thể tăng; bảng `RA_TAY_EVAL_v13.xlsx` (bản cuối) dành cho nhóm chấm tay toàn bộ 125 ca.
- v9 (03/11): 8/9 câu bị gắn cờ là judge chấm sai; câu còn lại là lời hứa 24 giờ mặc định, đã sửa ở v10.

## Baseline (`baseline_kb_in_context.json`, 20 câu của Demo Mobile)

| | Hỏi–đáp đúng | Token vào/câu | p50 | p95 |
|---|---|---|---|---|
| Nhồi cả kho tri thức vào prompt (73 nghìn token, 99.7% được cache) | 1.00 | 73,048 | 1.7 s | 2.6 s |
| RAG agent (v12) | 1.00 | 7,049 | 2.7 s | 3.7 s |

Chạy ngày 04/11 ngay sau v12, cả hai bên tuần tự. Thí nghiệm bật/tắt bước suy nghĩ ẩn (`agent_v4_thinking.json`)
được chạy trên cấu hình v4 ngày 30/10 và không chạy lại.

## Bộ giữ lại (held-out, `heldout_*_v13.json`, chạy một lần ngày 04/11)

Prompt được chỉnh qua 12 vòng trên bộ ở trên, nên điểm trên bộ đó có thể lạc quan. `eval/heldout/` gồm 30 câu hỏi–đáp
và 26 ca (12 chọn tool, 8 ngoài phạm vi, 6 tấn công) **mới**, viết sau khi đóng băng prompt v12 (bởi một sub-agent AI
không tham gia tinh chỉnh; xem `eval/heldout/README.md`), và chỉ chạy **một lần** trên hệ thống v13. Không có gì
trong hệ thống được sửa sau khi xem kết quả.

| Chỉ số | Bộ phát triển (v13) | Held-out |
|---|---|---|
| Recall@5 / MRR@10 / Recall@1 (cấu hình cuối) | 0.978 / 0.889 / 0.833 (90 câu) | **0.933 / 0.890 / 0.867** (30 câu) |
| Chọn đúng tool | 50/50 | **12/12** |
| Hỏi–đáp đúng | 39/40 | **27/30** (2 lượt AI Box treo, 1 câu sai) |
| Groundedness theo judge / sau khi AI rà | 0.966 / 0.989 | **0.875 / 0.975** (35 → 39 trên 40) |
| Từ chối đúng | 20/20 | **8/8** |
| Tấn công bị chặn | 15/15 | **6/6** |

- Truy hồi: 2/30 câu không có mục đúng trong top 5, cả hai là câu không dấu hoặc diễn đạt vòng ("tra hang hon 1 tuan
  roi ma van chua thay tien ve" → cần "Chưa nhận được tiền hoàn"). τ = 0.1 chặn cả 4 câu tài liệu không có.
- Câu sai `q-h-pol-016` ("mình hẹn shipper giao lùi… có được tính giao trễ không"): agent viết truy vấn bỏ mất chi
  tiết "hẹn giao lùi", nên không tìm thấy mục "Trường hợp không áp dụng". Bot nói tài liệu nó thấy không đề cập và đề
  nghị chuyển nhân viên: thiếu, không bịa. Không sửa, vì sửa theo bộ held-out sẽ làm nó mất giá trị.
- 5 câu bị judge gắn cờ được AI rà (`flagged_review_heldout_v13.json`, nhóm chưa xác nhận): 4 đúng, 1 thật sự tự tính
  khoảng ngày nhận hàng ("thứ Ba đến thứ Sáu tuần sau", sớm một ngày).
- Bảng để nhóm chấm tay: `RA_TAY_HELDOUT_v13.xlsx` (56 ca).
