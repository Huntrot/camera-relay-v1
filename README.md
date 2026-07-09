# 🚀 Camera Relay Server v1 — FastAPI + WebRTC SFU

Máy chủ trung chuyển luồng camera giám sát thời gian thực dựa trên giao thức WebRTC (SFU), được tối ưu hóa đặc biệt để triển khai dễ dàng trên các dịch vụ lưu trữ miễn phí như **Render.com (Free Tier)**.

---

## 🗺️ Cơ Chế Hoạt Động (How it works)

Phiên bản **v1** hoạt động theo luồng tín hiệu (Signaling) và luồng truyền thông (Media) trực tiếp qua cổng HTTP duy nhất (port 80/443):

```
[Camera RTSP] ──► [Laptop Xưởng (Publisher)]
                        │
                        ├─ 1. Bắt hình bằng OpenCV & nén luồng qua aiortc
                        ├─ 2. Gửi WebRTC Offer (SDP) qua HTTP POST
                        ▼
                [FastAPI Server (v1)]
                        │
                        ├─ 3. Tạo WebRTC Answer (SDP) trả lại cho Laptop
                        ├─ 4. Nhận và nắm giữ luồng video (MediaStreamTrack)
                        │
                        │ ── (Viewer gửi WebRTC Offer qua HTTP POST) ──►
                        ▼
                [Viewer (Web Trình duyệt)]
                        │
                        ├─ 5. Nhận WebRTC Answer (SDP) chứa luồng camera
                        └─ 6. Kết nối WebRTC trực tiếp qua TURN/STUN (Độ trễ < 0.5s)
```

### Điểm đặc biệt của kiến trúc:
* **Vượt qua giới hạn 1 cổng duy nhất trên Render:** WebRTC thông thường cần cổng UDP ngẫu nhiên để bắt tay. Máy chủ này đóng vai trò SFU, chuyển đổi bắt tay WebRTC thành 2 request HTTP POST (`/api/publish/offer` và `/api/view/offer`), nhờ đó chạy được trên cổng 80/443 tiêu chuẩn của Render.
* **Chuyển tiếp đa điểm (MediaRelay):** Sử dụng `MediaRelay` của thư viện `aiortc` để nhân bản luồng video nhận về từ 1 laptop và phát cho không giới hạn (N) người xem cùng lúc mà không làm tăng tải xử lý trên camera hoặc laptop xưởng.

---

## ⚠️ Hạn Chế Của Phiên Bản v1

Do được thiết kế cho mô hình tối giản nhất, phiên bản v1 có các hạn chế lớn sau:

1. **Chỉ hỗ trợ DUY NHẤT 1 Camera (Single Camera Only):**
   * Trạng thái kết nối của máy trạm đẩy luồng được lưu vào một biến toàn cục đơn lẻ trên RAM (`publisher_pc`, `publisher_video_track`).
   * Nếu có một laptop thứ hai kết nối vào đẩy luồng, laptop thứ nhất sẽ ngay lập tức bị máy chủ ngắt kết nối (kick out) để nhường quyền cho luồng mới.

2. **Không có Xác thực người xem (No Viewer Authentication):**
   * Bất kỳ ai có đường link Render của bạn đều có thể truy cập Web Viewer để xem camera trực tiếp. Máy chủ không có cơ chế chặn hoặc phân quyền tài khoản người xem.

3. **Không có Quản lý gói cước (No Role Limits):**
   * Không thể hạn chế số lượng camera hoặc giới hạn số phiên kết nối đồng thời của người xem (ví dụ: không giới hạn được tài khoản Free chỉ được xem 1 camera).

4. **Cấu hình ICE Server bị ghi cứng (Hardcoded STUN/TURN):**
   * Phía trình duyệt Web Viewer ghi cứng cấu hình STUN Server của Google. Trong trường hợp cần cập nhật thông tin TURN Server của bạn, bạn phải sửa đổi mã nguồn Frontend (`viewer.js`) và build/deploy lại toàn bộ.

5. **Chế độ ngủ của Render Free Tier (Sleep Mode):**
   * Nếu không có ai truy cập web trong vòng 15 phút, Render sẽ đưa máy chủ vào trạng thái ngủ đông. Người dùng truy cập sau đó sẽ phải chờ khoảng 50 giây để server khởi động lại.
