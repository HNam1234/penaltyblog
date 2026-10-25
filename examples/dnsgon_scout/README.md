# DNS Gọn: dự báo EPL, bảng kèo bóng đá và thông báo Telegram

Fork từ [martineastwood/penaltyblog](https://github.com/martineastwood/penaltyblog), MIT.
Nhánh `dnsgon-scout` thêm một worker nhẹ cho VPS 1 GB; giữ model Dixon–Coles
của upstream, dùng kết quả trận để fit xác suất 1X2. Giá thị trường không đi vào
huấn luyện. AI hội thoại không được tự bịa xác suất hoặc trận đấu.

## Đang làm gì

- Đọc lịch/kết quả EPL từ [OpenFootball, CC0](https://github.com/openfootball/football.json).
- Khi bật `today_only`, đọc lịch trong ngày Việt Nam từ JSON công khai của
  [Fantasy Premier League chính thức](https://fantasy.premierleague.com/fixtures).
  Cache lịch tối đa 15 phút, ánh xạ đủ 20 đội/380 trận, đối chiếu cặp đội và giờ
  đá với Gamma trước khi đọc giá. Không đọc được nguồn không được coi là không có trận.
  Bản triển khai tắt tin lịch chung (`send_daily_calendar: false`); dự báo đạt ngưỡng được gửi riêng.
- Bổ sung kết quả mới bằng API công khai Gamma, chỉ nhận khi tên hai đội/ngày,
  trạng thái kết thúc, tỷ số và cả ba hợp đồng 90 phút đã phân xử cùng khớp.
- Dùng API công khai Polymarket để đọc giá mua/bán và thanh khoản đầu sổ lệnh.
  Đây là một sàn dự đoán nhị phân, không phải đồng thuận odds từ nhiều nhà cái.
- Quét mỗi 5 phút; tối đa 20 slug trận và 4 nhóm sổ lệnh/lượt. Bản triển khai
  bật `today_only`: chỉ các trận chưa bắt đầu trong ngày theo giờ Việt Nam;
  chế độ tương thích cũ có cửa sổ 36 giờ.
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
Chưa có chấn thương, đội hình ra sân, xG hoặc odds nhiều nhà cái.
Mô hình xác suất đã kiểm định chỉ áp dụng EPL 1X2.

## Nguồn Fun88 và dữ liệu LoL, bổ sung 25/10/2026

### Kênh riêng chỉ nhận dự báo xác suất cao

`high_probability.py` dùng cùng bot DNS Gọn với một channel ID riêng, database/outbox
riêng, giữ lịch sử dự báo trước trận và các kết quả đối chiếu. Tận dụng dữ liệu
đã thu thập, không tạo thêm lượt lấy odds. Kênh nghiên cứu cũ tiếp tục nhận các
thẻ phân tích; chúng không được tự chuyển thành tín hiệu xác suất cao.

Điều kiện: EPL 1X2 trong ngày Việt Nam, xác suất model độc lập ≥75%,
`probability * decimal_odds - 1 ≥ 0.03` trước chi phí, quote/analysis ≤5 phút,
lịch FPL ≤15 phút và khớp ID/cặp đội/giờ đá, model ≤36 giờ, đủ lịch sử đội,
nhóm xác suất cao của kiểm định lịch sử đạt tiêu chuẩn hiện có.
Đối chiếu lại version và xác suất model, không tin phần trăm do nhà cái/AI trả về.

Không có trận qua bộ lọc thì không gửi đề xuất. Chấp/tài xỉu, giải nhỏ và LoL
chưa đủ kiểm định không được phát vào kênh này. EV dương từ model chưa chứng minh
lợi nhuận thực tế. Không có chức năng đặt cược, thay đổi tài khoản hoặc gửi tiền.

Tùy chọn config, giữ ngoài Git:

```json
{
  "high_probability_channel_id": "-100YOUR_SEPARATE_PRIVATE_CHANNEL_ID",
  "high_probability_database": "/your/state/high-probability.sqlite3"
}
```

Đã kiểm tra triển khai trên VPS: setup nhận đúng một lần ở kênh riêng,
bot chỉ có quyền đăng, chạy lại không gửi trùng. 205 kiểm tra Windows/Ubuntu qua;
4 trận hiện được chấm đều dưới 75%, chưa phát tín hiệu. Các dữ liệu test tổng hợp
chỉ chạy trong database tạm của unittest, không đưa vào Telegram production.

### Phân tích kèo và bối cảnh đội, cập nhật tiếp 25/10/2026

`market_analysis.py` và `analysis_reports.py` đã nối model EPL với bảng kèo Fun88:
1X2 giữ xác suất hiệu chỉnh riêng; AH/OU dùng phân bố tỷ số thô, tách rõ thắng đủ,
thắng nửa, hòa tiền, thua nửa và thua đủ. Báo xác suất có lời, giá hòa vốn,
EV trên đơn vị vốn trước chi phí. Chưa dùng EV để tuyên bố kèo có lợi thế.
Đối chiếu đúng giải/cặp đội/giờ đá với lịch FPL, từ chối model/quote cũ hoặc đội thiếu lịch sử.

`team_context.py` đọc cờ tình trạng cầu thủ từ FPL chính thức; chỉ hiển thị bối cảnh,
chưa tự sửa xác suất từ tin tức hoặc coi cờ FPL là đội hình xuất phát xác nhận.
Thẻ phân tích riêng chống gửi trùng và giữ phân tích trước trận đầu tiên.
Các snapshot hiện thời/context tạm chỉ giữ 7 ngày, giữ nguyên phân tích đầu tiên và marker tin đã gửi.

Lượt live: 4 trận, 28 cửa; một trận không đủ lịch sử. Đã gửi và đọc lại bốn thẻ thật
trong kênh riêng. 197 kiểm tra qua trên Windows/Ubuntu, parity 27 hợp đồng với wheel
1.13.1 sai số <1e-10, runtime thử khoảng 26 MB. Các dịch vụ thu chi hiện có vẫn active.

`evaluate_markets.py` kiểm tra thăm dò 362 trận mùa 2025/26 với 9 hợp đồng cố định,
fit lại hàng tháng bằng dữ liệu ba năm trước mốc. Không có odds lịch sử nên không tính ROI.
Tài 2.5 có Brier biến cố lời 0.2524, chưa tốt hơn mức 0.25 của dự báo 50/50;
nhóm ≥75% ở một số cửa home chỉ có 10 mẫu. AH/OU vẫn chưa có trạng thái đủ điều kiện tự khuyến nghị.

Xem [MARKET-RESEARCH.md](MARKET-RESEARCH.md) và [market-validation.json](market-validation.json).
Model độc lập cho các giải nhỏ và LoL chưa được triển khai; không áp kiểm định EPL sang đó.

- `fun88.py`: đọc bảng kèo **khách công khai**, không cần tài khoản nhà cái.
  Nhận 1X2, chấp châu Á và tài/xỉu 90 phút; chỉ trận chưa bắt đầu trong ngày Việt Nam.
  Loại Cyberbattle/bóng ảo, kèo phạt góc, market khóa, giá sai định dạng và hợp đồng mơ hồ.
- `fun88_watch.py`: mỗi lượt đọc 8 trang đầu + 2 trang luân phiên, đánh dấu coverage một phần;
  có thống kê kết quả gần đây theo đúng ID đội, cache 6 giờ, giữ dữ liệu quan sát 7 ngày.
  Nguồn chỉ trả một tập kết quả gần đây, không coi là đủ cả mùa hoặc đủ 5 trận mỗi đội.
- Tùy chọn thẻ theo dõi odds: tối đa 2/ngày, chỉ cửa có xác suất **suy từ odds** ≥75%,
  mỗi đội có ít nhất 3 kết quả gần đây. Đây **không phải thẻ dự báo độc lập hoặc kèo có lợi thế**;
  chúng không được ghi vào bảng dự báo để thổi phồng thành tích model.
- Dấu chấp được đối chiếu với formatter của giao diện: `home_line = -raw_handicap`,
  `away_line = raw_handicap`. Kèo phần tư tách hai nửa để phân biệt thắng/thua nửa và hòa tiền.
  Không quy đổi đơn giản hai odds chấp/tài xỉu thành xác suất thắng kèo.
- `esports_stats.py`: cache mô tả LoL từ [ChainCC Open Data](https://chaincc.lol/free/data),
  **CC BY 4.0**, có ghi công và mô tả phần biến đổi trong artifact.
  Ghép hai phía của cùng map, loại kết quả mâu thuẫn/thiếu phía; một map không phải một series.
  Dữ liệu thời gian không ghi timezone nên loại toàn bộ dòng cùng ngày hiện tại;
  không suy tỷ lệ thắng trận từ win rate 10 map.
- Lượt thử VPS đọc 197 trận/75 giải trong 10 trang; bản production đầu đọc 198 trận/76 giải,
  bổ sung thống kê 5 trận. RAM đỉnh riêng bộ đọc khách 26.868 KiB, dưới giới hạn service 96 MB.
  187 kiểm tra ngoại tuyến qua trên Windows và Ubuntu. Hai thẻ dữ liệu thật đã được nhận
  và đọc lại ở đúng kênh riêng, không có tin trùng sau triển khai lại.
- Nguồn LoL có 9.325 map đã ghép hợp lệ của 355 đội trong bản tải 25/10;
  **chưa có adapter odds LoL tự động hoặc model LoL được kiểm định**. Giao diện TF có
  kèo xem ở chế độ khách nhưng API trực tiếp trả HTTP 401; không vượt xác thực hoặc dùng
  cookie/tài khoản của người dùng. Không áp kết quả EPL sang LoL/giải bóng đá khác.

Xem [FUN88-NOTES.md](FUN88-NOTES.md) để biết phạm vi và bằng chứng kiểm tra giao thức.

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
  "today_only": true,
  "send_daily_calendar": false,
  "enable_fun88_guest": true,
  "enable_market_analysis": true,
  "send_market_analysis_cards": true,
  "fun88_pages": 8,
  "send_fun88_watch_cards": true,
  "telegram_token": "FILL_LOCALLY",
  "channel_id": "-100YOUR_PRIVATE_CHANNEL_ID",
  "direct_series_ids": ["10188"],
  "send_price_watch_cards": false
}
```

Chạy `python3 scout.py --config /your/private-config.json`. Chỉ thêm bot vào kênh
riêng do bạn quản lý, quyền đăng bài; không dùng `getUpdates` của bot khác đang chạy.
`--report` chỉ đọc báo cáo. Chạy định kỳ bằng systemd timer, tránh cron chồng lặp.

Cache thống kê LoL (stdlib, không cần key):
`python3 esports_stats.py --output /your/state/esports/team-stats.json`.
Trong bản triển khai, timer riêng chạy khoảng 07:40 và 19:40 Việt Nam;
file lỗi/cũ không thay thế cache hợp lệ. Đây là dữ liệu chuẩn bị để nối odds sau này,
chưa phát đề xuất LoL tự động.

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
