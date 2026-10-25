# Bằng chứng adapter đọc khách, 25/10/2026

Trang do người dùng đưa dẫn sang giao diện Fun88; tại mục Thể Thao Fun,
iframe công khai có URL `https://2s5sic5xgq.com/vit/home/index/today?thememode=2&siteid=wf01`.
Không khẳng định đây là API chính thức/ổn định của nhà cung cấp. Tên miền hoặc giao thức
có thể đổi; adapter dừng nhận dữ liệu khi schema, status hoặc đồng hồ nguồn không hợp lệ.

## Phạm vi đã đọc

- Guest-signature `cdb60a1`, trả ký hiệu dùng cho truy vấn khách, lấy lại mỗi lượt chạy.
- Paged listing `601ef09`, chỉ football, Decimal `OddsType=3`, full-time `PeriodId=1`,
  main line, `PlayerSpreadGroup=A`, `EventOrderBy=10`, `SelectedTimeZone=28` (GMT+7).
- Completed results `684d193`, giới hạn một competition ID đã xuất hiện trong bảng kèo.
- Không sử dụng endpoint đăng nhập, tài khoản, ví, lịch sử giao dịch hoặc đặt cược.
  Không cần `cc`, client secret, cookie hay token của người dùng.

Giới hạn 2 MB/phản hồi, timeout 10 giây, không tự chuyển hướng, không retry liên tục,
định mức số trang và số lượt lấy thống kê. API có thể trả các sự kiện bóng ảo dù đã
bật `IsWithoutVS`, nên adapter còn lọc loại sự kiện và tên giải/đội sau khi nhận.
`IsLive=true` trên trận chưa bắt đầu biểu thị khả năng cược live, không được dùng để
xác định trận đang diễn ra. Kiểm tra ngày giờ, Market, RBTimeStatus, tỷ số và trạng thái.

Nguồn chỉ có giờ server + thời điểm thu thập, không chứng minh giờ cập nhật từng quote.
Thẻ hết hạn sau 5 phút kể từ thu thập hoặc lúc bắt đầu trận, tùy thời điểm sớm hơn.
Không tuyên bố đây là tick feed tức thời.

## Đối chiếu shipped JavaScript

Đã dùng REA CLI 6.5.0 phân tích tĩnh các asset công khai và đối chiếu truy vấn thật
trên VPS. Asset app `app.b2e52c33.js` có SHA-256
`05353dc2cffb8e1a24c974a3471ef275b40c9baf5b235a27aee7ae984034181a`.
Vendor SHA-256 `dc2008a6709f96cc417c7be84ab666e2377871dfb8fcf35edb2a2b6651183144`.
Phân tích tĩnh chỉ cho biết cách client tạo yêu cầu; các lượt gọi khách thành công
mới xác nhận đã đọc được dữ liệu tại mốc kiểm tra.

Chỉ công bố adapter tự viết và ghi nhận giao thức; không phát hành lại bundle
độc quyền, dữ liệu phản hồi nguyên khối hoặc phiên trình duyệt trong fork/source ZIP.

## Odds và xác suất

Với 1X2 đầy đủ, baseline là `(1/odds_i) / sum(1/odds)`.
Đó là xác suất hàm ý của **một bảng giá**, không phải xác suất độc lập đã kiểm định.
Chưa có bằng chứng lợi nhuận sau phí, biến động giá và khả năng đặt đúng giá.
Mô hình Dixon–Coles EPL và số liệu 118/141 của nó giữ phạm vi riêng.

Thống kê Fun88 dùng đúng ID đội trong cùng giải, chỉ mẫu kết quả nguồn trả được;
không có kiểm chứng độ phủ đầy đủ, chấn thương hoặc đội hình xuất phát.
Thẻ theo dõi odds không được đưa vào mẫu chấm điểm model.

## LoL

Đã xem bảng TF guest: tại 25/10 mục LoL hôm nay báo 0; ngày 26/10 có 4 trận,
gồm cả BO1 và BO3, market cả trận khác market từng game.
API không có phiên khách hợp lệ trả 401; chưa tích hợp tự động endpoint này.
Cache ChainCC chỉ là dữ liệu thống kê map, không tạo đề xuất dựa trên tỷ lệ tự đoán.
