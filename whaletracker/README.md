# UNI Whale Tracker

Truy vết ví whale đã **tích góp ở đáy và xả ở đỉnh** trong chu kỳ UNI 2026, tách chúng khỏi ví
Market Maker và MEV bot, chấm điểm độ tin cậy copy-trade trên thang 5, rồi theo dõi 24/7 và
báo về email + Telegram khi ví đó giao dịch.

**Không dùng AI/LLM. Không cần API key nào.** Toàn bộ là luật + thống kê.

---

## Chu kỳ đang phân tích

| Mốc | Ngày | Giá | Ghi chú |
|---|---|---|---|
| Đáy chu kỳ | 2026-06-05/06 | **$2.316** | |
| Fake-out / quét thanh khoản | 2026-06-16/17 | 2.85 → **3.73** → 3.00 | volume 26.7M, cao nhất chu kỳ |
| Nền tích lũy | 2026-06-30 → 07-01 | ~$2.75–2.80 | |
| Bắt đầu markup (gap +18%) | 2026-07-02 | 2.79 → 3.29 | |
| **ĐỈNH** | **2026-07-31** | **$4.577** | ← 25 file CSV của bạn là đúng ngày này |
| Xả về | 2026-08-17 | $3.269 | |

---

## Cài & chạy

```bash
pip install -r requirements.txt

python -m wt all          # chạy tất cả, lần đầu ~40–60 phút
```

Hoặc từng bước (mỗi bước đều **resume được**, Ctrl+C rồi chạy lại không mất gì):

```bash
python -m wt ingest       # nạp 25 file CSV làm seed  (2,491 transfer / 635 địa chỉ)
python -m wt backfill     # kéo on-chain + giá về SQLite  ← lâu nhất
python -m wt classify     # phân loại địa chỉ
python -m wt ledger       # dựng sổ mua/bán + cost basis + PnL
python -m wt score        # chấm điểm copy-trade /5
python -m wt report       # xuất HTML + CSV + watchlist.json
python -m wt status       # xem tình trạng DB bất cứ lúc nào
```

Kết quả nằm trong `out/`:

| File | Nội dung |
|---|---|
| `report.html` | Báo cáo đầy đủ. Bấm vào **địa chỉ ví** → mở Etherscan lọc sẵn giao dịch UNI của ví đó. Bấm vào **chỗ khác trong dòng** → xem **tại sao** ví đó được điểm như vậy |
| `whales.csv` | Toàn bộ ví + chỉ số, để lọc trong Excel |
| `trades.csv` | Từng lệnh kèm giá, giá trị USD, độ tin cậy suy luận |
| `watchlist.json` | Danh sách ví cho module theo dõi đọc |

---

## Theo dõi & cảnh báo

```bash
python -m wt monitor --test-alert          # gửi 1 tin thử, kiểm tra kênh
python -m wt monitor --replay 2026-07-31   # PHÁT LẠI ngày đỉnh: tool lẽ ra đã báo gì?
python -m wt monitor                       # chạy liên tục
```

`--replay` là bài kiểm tra giá trị thật của tool: nó dùng dữ liệu đã có trong DB, **không tốn
thêm một request nào**, và cho bạn thấy nếu hôm 31/7 tool đã chạy thì bạn đã nhận được cảnh báo gì.

Bật kênh cảnh báo bằng biến môi trường (đừng ghi thẳng vào `config.json`):

```bash
export WT_TELEGRAM_ENABLED=true
export WT_TELEGRAM_TOKEN=...      # chat @BotFather → /newbot
export WT_TELEGRAM_CHAT_ID=...    # mở https://api.telegram.org/bot<TOKEN>/getUpdates

export WT_EMAIL_ENABLED=true
export WT_EMAIL_USERNAME=ban@gmail.com
export WT_EMAIL_APP_PASSWORD=...  # Gmail App Password 16 ký tự, KHÔNG phải mật khẩu thường
export WT_EMAIL_TO=ban@gmail.com
```

---

## Cách chấm điểm /5

| Hạng mục | Tối đa | Đo cái gì |
|---|---|---|
| Lợi nhuận | 1.25 | Phải đạt **cả** ROI tốt **và** số tiền đủ lớn (dùng `min` của hai, không phải trung bình) |
| Timing edge | 1.00 | `phân vị giá lúc bán − phân vị giá lúc mua`. Đây là thước đo "mua đáy bán đỉnh" |
| Lặp lại được | 1.00 | Số vòng mua-bán trọn vẹn × tỉ lệ thắng. Ăn may 1 lần không phải kỹ năng |
| Copy được | 0.75 | Gom từ từ nhiều giờ = bạn kịp bám theo. Bot MEV = 0 điểm |
| Tín hiệu sạch | 0.60 | Phải là EOA thật, có lập trường rõ ràng, không phải MM/CEX/contract |
| Còn sống | 0.40 | Ví đã bỏ đi thì copy ai |
| **Trừ điểm** | **−2.00** | Giá vốn không chắc, ví mới, chỉ 1 lệnh, chỉ chuyển CEX, chưa khép vòng nào, đang lỗ, bị đánh dấu lừa đảo |

**Đọc điểm:** ≥4.0 copy tự tin · 3.0–3.9 copy size nhỏ · 2.0–2.9 chỉ theo dõi · <2.0 bỏ qua.

Điểm `mm_score` riêng (càng cao càng giống Market Maker) dùng để **loại** khỏi danh sách whale
và trả lời câu "ai lái giá", **không phải** để copy.

---

## Nguồn dữ liệu (đều miễn phí, không key)

| Nguồn | Dùng làm gì |
|---|---|
| **Blockscout** `eth.blockscout.com` | Transfer on-chain, `is_contract`, nametag, `is_scam` |
| **Binance klines 1m** | Giá lịch sử. Dự phòng tự động: OKX → Kraken → CoinGecko |

> **Binance chặn IP Mỹ (HTTP 451).** Khi tạo Oracle Cloud instance hãy chọn region
> **Singapore / Tokyo / Frankfurt**. Nếu lỡ chọn US thì chuỗi dự phòng vẫn gánh được.

### Hai giới hạn kỹ thuật đã xử lý

1. **Blockscout trả tối đa 1000 record/lần và bỏ qua tham số `page`/`offset`.** Đã kiểm chứng
   `page=1` và `page=2` trả kết quả y hệt. Nên tool chia đôi block-range đến khi vừa; nếu **một
   block đơn lẻ** vẫn tràn (đã gặp thật: block `25652817`) thì đi vòng theo chiều transaction
   (endpoint đó có cursor thật). Nếu cả ba tầng đều thất bại → ghi vào bảng `truncated_blocks`
   và **báo cáo ra**, không bao giờ âm thầm bỏ qua.

2. **Log bụi.** Block `25652817` có >1000 log UNI mà **cả 1000 đều < 0.01 UNI**, 333 log giá trị
   đúng bằng 0 — đó là rác từ batch-swap của Balancer Vault. Tool lọc ở ngưỡng 0.01 UNI, **đếm
   và báo cáo** số lượng đã lọc.

---

## Deploy lên Oracle Cloud

```bash
sudo bash deploy/install_oracle.sh
sudo nano /etc/whaletracker.env        # điền thông tin cảnh báo
cd /opt/whaletracker && ./venv/bin/python -m wt all
sudo systemctl enable --now wt-monitor.service   # theo dõi liên tục
sudo systemctl enable --now wt-analyze.timer     # chấm điểm lại mỗi 6h
journalctl -u wt-monitor -f
```

Shape khuyến nghị: **VM.Standard.A1.Flex** (ARM, 4 OCPU / 24GB — always free).

---

## Ngân sách request

| Việc | Số call | Ghi chú |
|---|---|---|
| Backfill 138 ngày | ~3,300 | một lần, resume được |
| Đào sâu 200 ví | ~1,500 | một lần |
| Giá 1m 138 ngày | ~200 | một lần |
| Chạy lại định kỳ | ~50 | mỗi 6h |
| Monitor | ~1,900/ngày | 1 call / 45s |

Throttle 5 req/s theo từng host + exponential backoff + cache SQLite.

---

## Phát hiện từ lần chạy thật (cửa sổ 2026-04-01 → 08-17)

Dữ liệu: **872,018 transfer**, 199,136 nến giá 1 phút, 55,384 địa chỉ, **0 block bị mất**.

**1. "Top gom hàng" không phải whale.** Ba địa chỉ luân chuyển nhiều UNI nhất trong tháng 6
(2.1M / 1.7M / 1.4M UNI) đều là hạ tầng, không phải người đặt cược vào giá:

| Ví | Dấu vết | Thực chất |
|---|---|---|
| `0xb3ae2a74…` | 9.9M vào / 11.5M ra, **100% qua CEX**, 0 lệnh DEX, độ dứt khoát 7% | Ví vận hành sàn / bàn OTC |
| `0xe8736af1…` | 326 vòng, giãn cách lệnh **6 phút**, đấu MEV bot 5,183 lần, net ≈ 0 | Market Maker |
| `0x4266bbc1…` | 4.4M vào / 3.8M ra, 100% CEX, độ dứt khoát 8% | Ví vận hành sàn |

Tool đã tự tách **73 ví** loại này thành lớp `cex_shuttle`.

**2. Whale giao dịch thật qua DEX thì nhỏ hơn nhiều.** Tốt nhất: `0x5546174c0f`
(edge **+0.75**, ROI 31%, PnL $162k) và `0x09cf91d5e1` (PnL **$386k**, ROI 15%).

**3. Không ví nào đạt ≥3.0 điểm.** Nguyên nhân không phải thang điểm quá khắt khe mà là
**giới hạn bản chất của dữ liệu on-chain**: giai đoạn gom hàng ở đáy tháng 6 diễn ra **trong sàn**.
Ví rút UNI từ Binance ngày 05/06 thì on-chain chỉ thấy "rút", không thấy họ mua ở giá nào —
nên giá vốn không xác minh được và điểm bị trừ. Đây là điều **đúng đắn**: thà không có điểm
còn hơn có điểm sai.

Nếu bạn muốn theo dõi nhóm tốt nhất hiện có, hạ ngưỡng: `WT_MIN_SCORE=2.2`.

---

## Đọc kỹ trước khi đặt tiền

- **Chuyển token lên CEX ≠ đã bán.** Chỉ là suy đoán (conf 0.70). Ví có thể chỉ đang chuyển kho.
  Tool ghi rõ độ tin cậy từng suy luận, không giấu.
- **Không thấy được giao dịch trong sàn.** Whale chủ yếu trade trên Binance thì vô hình với tool này.
- **Chuyển sang một EOA lạ là KHÔNG BIẾT là gì** — có thể là ví phụ của chính chủ. Vì vậy
  `OTC_IN`/`OTC_OUT` chỉ làm đổi số dư, **tuyệt đối không sinh ra PnL ảo**.
- **Copy-trade luôn trễ.** Từ lúc tx lên block đến lúc bạn vào lệnh, giá đã chạy. Vì vậy hạng mục
  "copy được" ưu tiên ví gom từ từ, không ưu tiên ví bắn một phát.
- **Điểm 5/5 nghĩa là "quá khứ rất giỏi", không phải "chắc chắn thắng".**

---

## Cap nhat 2026-09-07 (gia $7.14)

Chu ky da di tiep rat xa so voi lan phan tich truoc:
day dieu chinh **$3.171 (14/08)** -> dinh **$7.483 (06/09)** = **+136% trong 23 ngay**.

**Lenh moi:**
```bash
python -m wt refresh          # cap nhat lich su vi qua Routescan (khong can key)
python -m wt report --entry 3.3   # bao cao kem phan tich danh muc theo gia von cua ban
```

**Nguon du lieu - bai hoc thuc te:** Blockscout gioi han theo IP theo NGAY. Sau ~5,000 request
no tra 429 cho MOI toc do, ke ca 0.25 req/s - ha toc do khong cuu duoc. Tool nay da co:
- `wt/http.py`: tu ha han toc mot nua moi lan gap 429 (1.5 -> 0.75 -> 0.38 -> 0.25 req/s)
- `wt/sources/routescan.py`: nguon du phong keyless, chiu tai tot hon
- `wt/sources/ethplorer.py`: nguon du phong thu hai (freekey)
- `wt/refresh.py`: chi cap nhat ~120 vi dang theo doi (~1 request/vi) thay vi quet ca chuoi khoi

**Canh bao ve Routescan:** tham so `tokenAddress` cua no KHONG loc that su - phan hoi van lan
token khac. Bat buoc loc lai phia client, neu khong se nap nham USDT/LIT vao so sach UNI.

**Rao chan pham vi du lieu:** `market.coverage()` phat hien khi du lieu on-chain khong con phu
toan thi truong, va bao cao hien canh bao. Da do duoc chenh lech 20 lan ve so transfer moi tuan
(78k khi quet day du -> 3k khi chi con nhom vi theo doi). Trinh bay so lieu mau nhu chi bao
toan thi truong la mot cach ra ket luan sai rat de mac.
