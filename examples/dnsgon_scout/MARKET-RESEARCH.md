# Repo phân tích đội và kèo — kiểm tra ngày 25/10/2026

## Lựa chọn sau khi đọc code

| Repo | Phù hợp với việc gì | Kết luận |
|---|---|---|
| [penaltyblog](https://github.com/martineastwood/penaltyblog) | Dixon–Coles/Poisson, phân bố tỷ số, 1X2, Asian handicap, tài/xỉu | Giữ làm lõi tính xác suất; MIT; đã có fork và wheel chạy trên VPS |
| [soccerdata](https://github.com/probberechts/soccerdata) | Đọc nguồn thống kê, matchsheet, đội hình qua ESPN và các nguồn khác | Apache-2.0 theo file LICENSE.rst; dùng làm tài liệu thiết kế bộ lấy dữ liệu. Không cài toàn bộ browser/pandas lên VPS nhỏ |
| [PandaSkill](https://github.com/PandaScore/PandaSkill) | Nghiên cứu sức mạnh tuyển thủ/đội LoL, có paper và code tái hiện | MIT; hướng nghiên cứu LoL phù hợp, chưa cài làm model production. Cần dữ liệu tuyển thủ/đội hình hiện tại và kiểm định 2026 |
| [Psionergy/League-of-Legends-Match-Predictor](https://github.com/Psionergy/League-of-Legends-Match-Predictor) | Feature roster/champion, Elo, model trước trận và live | Chỉ tham khảo; không thấy giấy phép. Không coi số liệu README là kết quả của bot này |
| [ProjektZero-LoL-Model](https://github.com/MRittinghouse/ProjektZero-LoL-Model) | Team/player Elo và TrueSkill | AGPL-3.0, lần push thấy được 28/02/2022; không chọn làm nền triển khai mới |
| [Jimonhitori/lol_predictor](https://github.com/Jimonhitori/lol_predictor) | Pipeline LoL được cập nhật rất gần đây | Chưa tìm thấy giấy phép theo metadata; chưa đưa source vào fork hoặc chạy code |

Các repo được tải để đọc source, không chạy installer hoặc file model pickle/joblib của bên lạ.
License phần mềm không đồng nghĩa quyền sử dụng không giới hạn dữ liệu từ mọi website mà scraper hỗ trợ.

## Những điểm đã kiểm tra trong source

**penaltyblog:** `football_probability_grid.py` tính các thị trường từ cùng một ma trận tỷ số.
Các hàm quarter-line gộp theo tỷ trọng nửa tiền cược. Bộ phân tích mới giữ thêm
5 trạng thái của toàn bộ khoản cược để phân biệt khả năng có lời với khả năng thắng đủ.
Đã đối chiếu 27 hợp đồng AH/OU với wheel chính thức 1.13.1, sai số lớn nhất
`7.77e-16`. Đây là xác minh công thức, không xác minh model dự báo đúng.
[Tài liệu chính thức](https://penaltyblog.readthedocs.io/en/latest/models/football_prob_grid.html).

**soccerdata:** `soccerdata/espn.py` có `read_schedule`, `read_matchsheet`, `read_lineup`.
Đã đọc được ESPN scoreboard và summary thật, gồm recent games; không phải mọi trận
có sẵn đội hình hoặc injury feed. Package hiện có dependency trình duyệt và pandas,
nên bot tiếp tục dùng HTTP JSON giới hạn dung lượng. Tin lực lượng EPL được lấy
trực tiếp từ FPL, còn dữ liệu giải nhỏ vẫn từ đúng ID đội của Fun88.
[Reader ESPN](https://github.com/probberechts/soccerdata/blob/master/soccerdata/espn.py).

**PandaSkill:** repo gắn với paper nghiên cứu và có dữ liệu tái hiện, mô hình đánh giá
tuyển thủ. Đây không phải chứng nhận bot cá cược có lời, không phải feed roster 2026
hoặc API odds nhà cái. Không triển khai nguyên requirements có thư viện GPU/dashboard
lên VPS 1 GB. [Paper](https://arxiv.org/abs/2501.10049),
[giấy phép](https://github.com/PandaScore/PandaSkill/blob/main/LICENSE).

**Psionergy:** source hiện chia train/test theo ngày, song `backtest.py` nạp một model
bundle sẵn và không tự kiểm tra thời điểm train của bundle với từng trận test.
Feature builder dùng roster/champion của trận, nên cần phân biệt dự báo có draft
với dự báo trước draft. Chưa có giấy phép và bằng chứng tái chạy độc lập tương ứng
mọi thành tích README; chưa dùng con số accuracy quảng bá để đặt ngưỡng cho bot.

## Đã nối vào VPS và Telegram

- `market_analysis.py`: đọc mô hình độc lập, tính đủ 7 cửa khi có 1X2 + chấp + tài/xỉu.
- Đối chiếu đúng giải EPL, tên đội trong allowlist, cặp đội và giờ đá với FPL;
  model cũ, quote cũ, trận đã bắt đầu hoặc đội ít lịch sử đều bị giữ lại.
- Xác suất 1X2 giữ hiệu chỉnh đã có. AH/OU dùng phân bố tỷ số thô, **không lấy nhiệt độ
  hiệu chỉnh 1X2 để tự nhận đã hiệu chỉnh thị trường khác**.
- `team_context.py`: lấy cờ chấn thương/treo giò/nghi ngờ ra sân từ FPL, có thời điểm
  lấy và thời điểm nguồn cập nhật trong JSON; chưa xác nhận đội hình xuất phát.
  Tin này hiển thị làm bối cảnh, chưa tự cộng/trừ xác suất.
- Phân tích trước trận đầu tiên được giữ lại; thẻ có khóa chống gửi trùng và tuổi quote ≤5 phút.
  Các thẻ nghiên cứu không trộn vào bảng chấm điểm của dự báo EPL cũ.

Lượt xác minh VPS thật: **4 trận / 28 cửa**, một trận bị loại vì chưa đủ lịch sử đội.
Đã gửi và đọc lại [thẻ 12](https://t.me/c/4204401124/12), [13](https://t.me/c/4204401124/13),
[14](https://t.me/c/4204401124/14), [15](https://t.me/c/4204401124/15) trong kênh riêng.
RAM đỉnh của lượt xác minh 26.804 KiB; 197 kiểm tra qua trên Windows và Ubuntu.
Bot thu chi, receiver và gateway vẫn active; không mở quyền đọc dữ liệu tài chính.

## Đọc đúng phần trăm và EV

Mỗi cửa báo xác suất thắng đủ / thắng nửa / hòa tiền / thua nửa / thua đủ.
`P(có lời)` bằng xác suất thắng đủ + thắng nửa, không bao gồm hòa tiền.

Với odds decimal `O`, lợi nhuận kỳ vọng trước chi phí cho 1 đơn vị vốn:

`EV = (P(thắng đủ) + 0.5 P(thắng nửa)) × (O−1) − P(thua đủ) − 0.5 P(thua nửa)`.

Giá hòa vốn theo model là `1 + trọng số thua / trọng số thắng`.
EV dương vẫn có thể do sai model, thiếu đội hình, quote không còn đặt được hoặc luật
thanh toán khác; chưa có kiểm định lợi nhuận thực tế.

## Kiểm tra hồi cứu AH/OU mới

Đã chạy 362 trận đủ điều kiện của mùa 2025/26, với 9 hợp đồng cố định được chọn trước.
Fit lại đầu mỗi tháng, chỉ dùng ba năm trước ngày đầu tháng; không dùng kết quả tương lai.
18 trận thiếu lịch sử bị loại. Dữ liệu là bản tải hiện tại, không phải feed đã lưu tại từng ngày.

- Tài 2.5: Brier cho biến cố có lời khoảng **0.2524**, trong khi đoán 50% luôn cho Brier 0.25.
  Như vậy kiểm tra này chưa cho thấy phần tài/xỉu thô đáng tin để tự phát tín hiệu lợi thế.
- Nhóm xác suất có lời ≥75% ở một số kèo home có 10 mẫu, 9 kết quả có lời;
  mẫu nhỏ, các hợp đồng dùng cùng trận nên không được cộng chúng thành nhiều lần kiểm định độc lập.
- Không có odds lịch sử đúng mốc, nên **không tính ROI**, không suy lợi nhuận từ tỷ lệ đúng.
- Nhịp fit hàng tháng của kiểm tra thăm dò khác model live fit hàng ngày.
  Kết quả này không tự nâng trạng thái AH/OU thành model đã được chứng nhận.

Chi tiết: `market-validation.json`, `market-retrospective.json`,
`native-parity.json`, `runtime-verification.json`, `channel-readback.json`.

## Phần còn thiếu

**Giải nhỏ:** đã có odds và một tập thống kê gần đây, nhưng chưa có model độc lập
được huấn luyện/kiểm định riêng cho từng giải. Không gán kết quả kiểm định EPL sang
Sweden Ettan, bóng đá nữ, China League 1 hay các giải khác.

**LoL:** đã có cache 355 đội; chưa có odds tự động hoạt động, chưa có model 2026
được kiểm định với roster, patch, phe, trước/sau draft và hợp đồng map/series phù hợp.
PandaSkill là repo chọn để nghiên cứu bước này, chưa được báo là đã triển khai.

## Dấu mốc source đã đọc

| Repo | Commit |
|---|---|
| soccerdata | `b507432742119ba3897533895c92ac3a4f2b9259` |
| Psionergy | `45373ef92a947be192494fed0c0662d0df700236` |
| PandaSkill | `41dc6423d95fdfc9e7f927018e7fbcf189555b5e` |

Source tự viết nằm trong fork `HNam1234/penaltyblog`, nhánh `dnsgon-scout`,
thư mục `examples/dnsgon_scout`. Không phát hành lại bundle độc quyền của nhà cái.
