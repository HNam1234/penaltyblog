# Agent thể thao Luna trên VPS

Đã triển khai và xác minh ngày **25/10/2026**, giờ Việt Nam.

- **Model:** `gpt-6-luna`, reasoning `xhigh` (Extra high), Codex CLI 0.164.0.
- **Lịch:** phút 00 và 30 mỗi giờ, thêm tối đa 10 giây độ trễ; systemd persistent,
  tự bật sau reboot. Chạy trên VPS, không cần mở Codex hay bật PC.
- **Kênh:** [DNS Gọn — Kèo xác suất cao](https://t.me/c/4472439508/3),
  cùng bot `@dnsgon_nam_194028_bot`. Đã đọc lại thông báo bật agent thật, đúng một lần.
- Bộ lấy dữ liệu hiện có vẫn chạy 5 phút/lượt để giữ giá/lịch mới; agent xét phát
  tín hiệu riêng mỗi 30 phút. Không có cửa đạt điều kiện thì không nhắn lặp.

## Điều kiện và cách giải thích

Chỉ adapter đã đủ điều kiện mới đưa candidate cho Luna: model thống kê độc lập
chấm xác suất ít nhất 75%, EV theo model ít nhất +3% trước chi phí, quote/analysis
không quá 5 phút, đúng lịch và hợp đồng, đủ dữ liệu lịch sử, trận chưa bắt đầu.

Luna rà soát các candidate và chọn những luận điểm có trong dữ kiện: xác suất,
giá, nhóm kiểm định lịch sử, số trận huấn luyện, bàn kỳ vọng và độ mới dữ liệu.
Ứng dụng dựng phần giải thích từ dữ kiện cố định; Luna không tự tạo hoặc sửa
phần trăm, odds, tin chấn thương hay đội hình. Tin gửi có lựa chọn, xác suất,
odds, EV, lý do chọn, điểm cần lưu ý, giờ lấy giá và mã dự báo bất biến.

Sau khi AI trả lời, pipeline tính lại điều kiện, đối chiếu hash dữ kiện, tuổi giá
và giờ bắt đầu. Giá/model thay đổi hoặc nguồn đã cũ thì giữ lại. AI lỗi/hết hạn
mức thì không tự phát candidate. Các dự báo được lưu để chấm cả đúng và sai;
không tự đặt cược hoặc chuyển tiền. EV dương từ model chưa chứng minh có lời.

## Phạm vi thật

Agent rà **8 nhóm model** mỗi lượt. Hiện chỉ **EPL 1X2** được phép phát tín hiệu.
EPL chấp/tài xỉu, LoL, Dota 2, CS2, NBA, tennis và bóng chuyền có registry nêu
rõ điều kiện còn thiếu; chúng chưa được bật thành tín hiệu xác suất cao.

Đã đưa **91 file source/tài liệu từ 7 repo** lên VPS tại
`/opt/dnsgon-sports/research-sources`, kèm commit và SHA-256 từng file.
Đây là snapshot source để nghiên cứu, **không phải bảy ứng dụng đã cài đủ dependency,
train xong và có feed live**. File giấy phép được giữ riêng; source bên ngoài chưa
có license hoặc có CC BY-NC-SA không bị gộp vào code MIT của fork.

Code LoL tự viết nằm ở `/opt/dnsgon-sports/multisport-lab`, gồm baseline Elo,
bộ tính BO3/BO5 và kiểm tra. Job nghiên cứu tải và đánh giá dữ liệu LoL hai lần/ngày
lúc **06:45 và 18:45 VN**, thêm tối đa 45 giây độ trễ; lưu vào
`/var/lib/dnsgon-sports/research/lol`. Job này không cấp quyền phát LoL.
OpenSkill source MIT đã import và thử `predict_win` trên VPS thành công;
chưa có model CS2 đã được train từ đó.

## Cách vận hành

Chạy một lượt ngay bằng tài khoản quản trị VPS:

```sh
systemctl start dnsgon-sports-agent.service
```

Đọc trạng thái và lượt hẹn tiếp theo:

```sh
runuser -u sports-scout -- python3 /opt/dnsgon-sports/sports_agent.py --status
systemctl list-timers dnsgon-sports-agent.timer dnsgon-lol-research.timer
journalctl -u dnsgon-sports-agent.service -n 10 --no-pager
```

Các lệnh trạng thái chỉ trả thông tin model/lượt chạy, không trả token. Không
đưa file `/etc/dnsgon-sports.json` hoặc dữ liệu xác thực Codex vào Git.

## Tách quyền

Coordinator chạy dưới `sports-scout`; chỉ bên này giữ quyền gửi vào kênh định sẵn.
Luna chạy dưới `codex-coach`, giao tiếp bằng Unix socket chỉ nhận đúng UID của
coordinator. CLI dùng session ephemeral, schema JSON, tắt shell, app, web search
và multi-agent; không nhận token Telegram hoặc dữ liệu tài chính trong đầu vào.
Giới hạn RAM: coordinator 96 MiB, inference 320 MiB, job LoL 160 MiB.
Đăng nhập Codex hiện có được tái sử dụng trên VPS; vẫn phụ thuộc phiên đăng nhập,
hạn mức tài khoản, mạng và thời gian VPS được duy trì.

## Bằng chứng kiểm tra

- **213 kiểm tra** worker/agent qua trên Windows và Ubuntu.
- **6 kiểm tra** LoL/series qua trên VPS.
- Đã gọi Luna thật với schema candidate giả trong bộ nhớ để kiểm tra quyết định
  và định dạng thẻ; **không gửi candidate giả lên Telegram**.
- Hash của đủ 91 file source trên VPS khớp manifest.
- Bot thu chi, receiver, gateway, coach và các timer cũ đều active sau triển khai.
- Lượt đầu đọc 4 phân tích EPL, cả 4 dưới ngưỡng; **0 dự báo kèo thật được tạo**.
- Setup agent đã gửi đúng một lần, message ID 3, đã đọc lại từ Telegram Desktop.

Chi tiết: `runtime-verification.json`, `channel-readback.json`, cùng bản
`deployment.json` trong thư mục `outputs/sports-scout`.

Triển khai hiện tại có backup cấu hình, code và hai database thể thao dưới
`/root/dnsgon-backups/`. Không thay đổi database hoặc quyền của bot thu chi.

Tài liệu CLI chính thức được kiểm tra khi triển khai:
[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
