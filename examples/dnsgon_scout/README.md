# DNS Gọn: dự báo EPL và thông báo Telegram

Fork từ [martineastwood/penaltyblog](https://github.com/martineastwood/penaltyblog), MIT.
Nhánh `dnsgon-scout` thêm một worker nhẹ cho VPS 1 GB; giữ model Dixon–Coles
của upstream, dùng kết quả trận để fit xác suất 1X2. Giá thị trường không đi vào
huấn luyện. AI hội thoại không được tự bịa xác suất hoặc trận đấu.

## Đang làm gì

- Đọc lịch/kết quả EPL từ [OpenFootball, CC0](https://github.com/openfootball/football.json).
- Bổ sung kết quả mới bằng API công khai Gamma, chỉ nhận khi tên hai đội/ngày,
  trạng thái kết thúc, tỷ số và cả ba hợp đồng 90 phút đã phân xử cùng khớp.
- Dùng API công khai Polymarket để đọc giá mua/bán và thanh khoản đầu sổ lệnh.
  Đây là một sàn dự đoán nhị phân, không phải đồng thuận odds từ nhiều nhà cái.
- Quét mỗi 5 phút; tối đa 20 slug trận và 4 nhóm sổ lệnh/lượt, trong 36 giờ tới.
  Phạm vi có giới hạn, lịch đổi ngày/mã đội có thể làm bỏ sót trận.
- Lưu dự báo đầu tiên bất biến trước trận, hash model, thời gian dữ liệu, cả đúng/sai;
  đối chiếu kết quả và báo cáo Brier/log loss/tỷ lệ đúng của mẫu live.
- Chỉ gửi thẻ thử nghiệm khi xác suất model ≥75%, chênh model − giá mua ≥0,03
  **trước phí**, thanh khoản đầu sổ đủ đơn vị tối thiểu, giá ≤5 phút tuổi,
  hai đội có ≥15 trận, dữ liệu kết quả đủ mới và không thiếu trận quá khứ của hai đội.
  Chênh này không phải lợi thế sau phí đã xác minh.
- Chống gửi lặp; phản hồi gửi không chắc chắn không được tự gửi lại.
- Cập nhật kết quả/fit lại 4 lần/ngày. Thu thập/Telegram dùng Python stdlib;
  NumPy/SciPy chỉ chạy trong tác vụ huấn luyện riêng.

Không đặt cược, không có wallet, không nhập tài khoản nhà cái. Không xử lý ngân hàng.
Chưa có chấn thương, đội hình ra sân, xG hoặc odds nhiều nhà cái; chưa hỗ trợ đa môn.

## Kiểm định ngày 25/10/2026

Xem [validation.json](validation.json). Sau khi chuẩn hóa tên CLB xuyên các mùa:

| Mục | Kết quả |
|---|---:|
| Mẫu ngoài giai đoạn hiệu chỉnh | 2.190 trận |
| Nhóm xác suất model ≥75% | 141 trận |
| Chọn đúng trong nhóm đó | 118/141 = 83,69% |
| Khoảng Wilson 95% | 76,71–88,88% |
| Brier 1X2, toàn mẫu | 0,58402 |
| Log loss, toàn mẫu | 0,98164 |

Quy trình: warm-up từ 2016; chọn nhiệt độ 1,05 chỉ trên giai đoạn 2019/20;
test 01/08/2020–24/05/2026. Fit lại theo ngày, chỉ dùng trận có ngày trước ngày
dự báo; cửa sổ 3 năm, trọng số giảm nửa sau 365 ngày. Không dùng kết quả cùng ngày.
Đội mới/không đủ lịch sử bị loại. Tên CLB cũ được ánh xạ rõ ràng, không fuzzy match.

Đây là **hồi cứu theo thứ tự thời gian**, trên bản lưu dữ liệu tải hiện nay;
không có bằng chứng các bản dữ liệu tương ứng đã được nhận tại từng ngày lịch sử.
Số liệu có thể được nguồn sửa lại. Khoảng Wilson mô tả mẫu, chưa điều chỉnh phụ thuộc
giữa các trận cùng đội. Nhóm ≥75% được tính trước bước lọc theo giá, không phải
backtest lợi nhuận của các tin Telegram. Chưa kiểm định ROI, phí, trượt giá hay
khả năng khớp lệnh. Tỷ lệ đúng lịch sử không bảo đảm xác suất tương lai.

## Chạy lại trên Ubuntu 22.04, không cần API key dữ liệu

Từ thư mục gốc fork:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: --no-deps -r examples/dnsgon_scout/requirements-headless.txt
.venv/bin/python examples/dnsgon_scout/setup_headless.py
cd examples/dnsgon_scout
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
../../.venv/bin/python fetch_history.py --data /your/state/model-data
../../.venv/bin/python train.py --data /your/state/model-data --output /your/state/model
../../.venv/bin/python -m unittest discover -s . -q
```

`setup_headless.py` chỉ thay hai entry point để không import toàn bộ scraper/plotting;
native kernels là wheel chính thức penaltyblog **1.13.1**, không tuyên bố được biên dịch
từ Git HEAD của fork. Bộ trainer đối chiếu kết quả export stdlib với native model
mỗi lần fit, sai số tối đa 1e-8. Không cài từ nguồn/GCC trên VPS nhỏ.

Tạo file config **ngoài Git**, quyền 0600, với các trường:

```json
{
  "mode": "polymarket_model",
  "database": "/your/state/scout.sqlite3",
  "model_file": "/your/state/model/model.json",
  "model_results_file": "/your/state/model-data/resolved-live-results.json",
  "model_data_dir": "/your/state/model-data",
  "telegram_token": "FILL_LOCALLY",
  "channel_id": "-100YOUR_PRIVATE_CHANNEL_ID",
  "direct_series_ids": ["10188"],
  "send_price_watch_cards": false
}
```

Chạy `python3 scout.py --config /your/private-config.json`. Chỉ thêm bot vào kênh
riêng do bạn quản lý, quyền đăng bài; không dùng `getUpdates` của bot khác đang chạy.
`--report` chỉ đọc báo cáo. Chạy định kỳ bằng systemd timer, tránh cron chồng lặp.

Huấn luyện định kỳ:
`../../.venv/bin/python update_model.py --data /your/state/model-data --output /your/state/model`.
Giới hạn bộ quét 96 MB, trainer 256 MB/25% CPU; source root-owned read-only,
state riêng user `sports-scout`. Worker không cần root, không cần đọc database tài chính.
Kết quả fit được ghi model.json bằng rename nguyên tử; lỗi fit giữ model cũ,
chốt độ mới sẽ dừng đề xuất khi model/dữ liệu cũ.

## Repo đã đối chiếu

| Repo | Nhận xét từ source |
|---|---|
| [penaltyblog](https://github.com/martineastwood/penaltyblog) | Chọn: MIT, Dixon–Coles có native kernel, export được cho worker nhẹ |
| [theopenmodel](https://github.com/Hicruben/theopenmodel) | MIT; công thức away-goals/score grid giữa live và backtest không giống nhau ở bản đọc, chưa dùng thành tích quảng bá |
| [golazo](https://github.com/ohugonnot/golazo) | MIT; mô hình hiệu chỉnh về xác suất từ odds, không phù hợp vai trò dự báo độc lập ở đây |

Tham chiếu upstream lúc audit: penaltyblog `6870f0e762e9cba35fa722dc14a8daeaf8bc6134`,
theopenmodel `a4f1239a2bc8d0550408a5f8ee1cebb2f1815138`,
golazo `e163e9ad9a1183727469779d3194d155d472d688`.

Đọc API: [Gamma events](https://docs.polymarket.com/api-reference/events/list-events),
[goal models](https://penaltyblog.readthedocs.io/en/latest/models/overview.html).
Mã thêm trong thư mục này được phát hành theo MIT của repo.
