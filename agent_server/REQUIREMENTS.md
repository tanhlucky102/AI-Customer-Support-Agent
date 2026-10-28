# Yêu cầu chatbot hỗ trợ khách hàng — bản demo

## 1. Quyền sử dụng

- Khách phải đăng nhập trên website trước khi gửi **bất kỳ** câu hỏi nào, kể cả FAQ.
- Backend website cấp JWT chat ngắn hạn sau khi xác thực phiên đăng nhập. Widget chỉ nhận JWT; khóa AI, khóa đồng bộ FAQ và khóa gọi API shop không xuất hiện trong trình duyệt.
- Agent Server xác minh chữ ký, `iss`, `aud`, `sub`, `iat`, `exp` của JWT ở mỗi yêu cầu chat hoặc đọc lịch sử. Mọi dữ liệu đơn hàng và hội thoại gắn với `sub`, không nhận `customer_id` từ trình duyệt.
- Khi chưa đăng nhập, widget hiển thị lời nhắc và khóa ô nhập. API trả 401 nếu thiếu hoặc sai JWT.

## 2. Lịch sử hội thoại

- Lưu câu hỏi, câu trả lời và liên kết nguồn trong SQLite theo `shop_id`, `customer_id` và `conversation_id`; dữ liệu còn sau khi khởi động lại server.
- Khách có thể tạo chat mới, xem danh sách chat trước và mở lại nội dung để tiếp tục hỏi trong cùng cuộc trò chuyện.
- Khách khác hoặc shop khác không thể đọc, tiếp tục hay gửi phản hồi cho hội thoại không thuộc mình. Đăng xuất xóa nội dung và token đang hiển thị trong widget; lịch sử trên server được giữ để xem sau khi đăng nhập lại.
- Model chỉ xem tối đa sáu tin nhắn gần nhất của **cùng hội thoại** để hiểu câu hỏi nối tiếp; không có bộ nhớ dài hạn/RAG.

## 3. Trả lời hỗ trợ

- Lời chào: chào lại ngắn gọn, giới thiệu có thể hỗ trợ sản phẩm, ưu đãi, sự kiện đã công bố, chính sách và đơn hàng; nếu lời chào kèm câu hỏi thì trả lời câu hỏi đó.
- Câu hỏi chính sách: chọn FAQ đã đồng bộ của đúng shop, trả nội dung và nguồn đã lưu.
- Câu hỏi đơn hàng: dùng danh tính đã xác thực để gọi API shop; mã đơn được đối chiếu với câu khách nhập. Nếu khách mô tả đơn mà không nêu mã, liệt kê các đơn gần đây để họ chọn.
- Câu hỏi sản phẩm trên shop demo: gọi API danh mục sản phẩm hiện tại để trả tên, giá, tồn kho hoặc thông số có trong dữ liệu. Liên kết dẫn đến trang sản phẩm tương ứng.
- Câu hỏi sale/mã giảm giá: đọc mã mà checkout thực sự chấp nhận và sản phẩm đang giảm giá từ API shop demo; website khách khác đẩy bản ưu đãi đã công bố qua `PUT /v1/shops/{shop_id}/offers`. Không suy diễn mức giảm hoặc hạn dùng từ banner quảng cáo.
- Câu hỏi sự kiện: dùng lịch sự kiện do shop cung cấp, gồm thời gian bắt đầu/kết thúc và nguồn. Khi chỉ có banner hoặc chưa có lịch xác nhận, nói rõ chưa xác định được thời gian và điều kiện; hướng khách xem website hoặc liên hệ shop.
- Câu hỏi ngoài phạm vi shop (ví dụ chính trị, thời tiết, tin tức): từ chối trao đổi ngắn gọn và gợi ý lại phạm vi hỗ trợ khách hàng. Câu hỏi chính trị được chặn trước khi gọi model.
- Nếu FAQ/API không chứa thông tin được hỏi, không tìm thấy sản phẩm, hoặc API lỗi, bot nói rõ **chưa xác định được** và hướng khách **liên hệ trực tiếp với shop**. Không suy đoán giá, tồn kho, đặc tính sản phẩm hay trạng thái đơn.
- AI Box hiện dùng để hiểu ý định và câu hỏi nối tiếp; dữ kiện trong câu trả lời phải đến từ FAQ hoặc API shop. Nếu AI Box lỗi, bộ quy tắc cũ xử lý được các câu cơ bản và cũng phải trả lời trung thực khi thiếu dữ liệu.

## 4. Phạm vi bản demo

- API sản phẩm, đơn hàng và ưu đãi hiện nối với `shop_demo`. Website khác có thể đồng bộ ưu đãi/sự kiện đã công bố; sản phẩm và đơn hàng cần tích hợp API riêng trước khi chatbot trả lời các loại dữ liệu đó.
- Chưa có RAG, tra sản phẩm theo ngữ nghĩa sâu, tìm đơn chính xác theo mô tả/ngày mua, quản trị lịch sử, xóa dữ liệu, hay kết nối Agent Core trên máy thành viên nhóm.
