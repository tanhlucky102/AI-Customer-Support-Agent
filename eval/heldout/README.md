# Bộ đánh giá held-out (giữ lại, chưa từng dùng để tinh chỉnh)

- **Mục đích:** `eval/retrieval.jsonl` và `eval/cases_agent.jsonl` đã được dùng để tinh chỉnh prompt qua 12 vòng nên kết quả trên đó có thể bị lạc quan. Bộ này gồm các câu hỏi mới, chưa từng đưa vào hệ thống, để đo hệ thống cuối cùng một cách khách quan.
- **Người viết:** một AI sub-agent không tham gia các vòng tinh chỉnh. Sub-agent chỉ đọc hai file cũ để chép đúng định dạng JSON, không dùng lại hay diễn đạt lại câu hỏi cũ; phần lớn câu hỏi nhắm vào các mục (section) mà bộ cũ chưa hỏi.
- **Thời điểm viết:** 04/11/2026, sau khi prompt đã được đóng băng (bản v12).
- **Nội dung:**
  - `retrieval.jsonl`: 30 câu hỏi QA (20 câu `demo_shop`, 10 câu `second_shop`). Văn phong chat thật: 6 câu không dấu, một số câu viết tắt (ck, sp, đt, ko, đc, bh), 4 câu tiếng Anh.
  - `cases_agent.jsonl`: 26 ca, gồm 12 ca `tool`, 8 ca `out_of_scope` (4 `unrelated`, 4 `unknown`) và 6 ca `attack`.
- **Cách kiểm chứng:**
  - Với mỗi câu QA, đã mở file `data/kb/<shop>/<doc>.md` và đọc nội dung mục kỳ vọng để chắc chắn mục đó trả lời đúng câu hỏi. Tên mục (`## `) được so khớp chính xác bằng script.
  - Mã đơn và mã khách đã đối chiếu với `shop_demo/shop.db` và `examples/second_shop/app.py`. Chuỗi `forbidden` lấy từ dữ liệu thật của khách khác (C0002, K1002) và từ system prompt.
  - Mỗi ca `unknown` đã được grep từ khoá trong knowledge base (không có kết quả liên quan) và chạy `rag.search` với `apply_threshold=False`. Điểm cao nhất ghi trong trường `checked_top_score`, mọi ca đều dưới ngưỡng τ = 0,1.
  - Script kiểm tra tự động: mỗi dòng parse được, `id` không trùng, mọi cặp (doc, section) tồn tại, mọi mã đơn tồn tại và thuộc đúng khách.
  - Không chạy agent hay gọi API LLM nào khi viết bộ này: không câu nào được chọn hay sửa dựa trên kết quả của hệ thống.
- **Quy tắc sử dụng:** chỉ chạy **một lần duy nhất** trên hệ thống cuối cùng và báo cáo nguyên kết quả. **Không** dùng bộ này để sửa prompt, tool hay tham số tìm kiếm. Nếu đã sửa hệ thống sau khi xem kết quả, bộ này không còn là held-out nữa và phải ghi rõ điều đó trong báo cáo.
