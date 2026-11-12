"""Phan tich BTC - thi truong chung quyet dinh phan lon huong cua altcoin.

Lay nen ngay tu Binance (keyless) luc tao bao cao. Khong luu vao DB: BTC chi dung
de doi chieu, khong phai du lieu cot loi cua tool.

Phan "tuong tu lich su" khop 6 dieu kien cung luc (RSI, khoang cach MA50, khoang cach dinh 90n,
tren/duoi MA200, MA20, MA50) voi dung sai cu dinh TRUOC khi nhin ket qua, cac su kien cach
nhau >=10 ngay de khong dem 1 dot thanh nhieu su kien. Luon kem duong co so (moi ngay) de
biet ket qua co khac gi so voi viec chon ngay ngau nhien.
"""
from __future__ import annotations

import json
import statistics
import urllib.request

URL = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&startTime={s}&limit=1000"


def fetch_daily() -> list[dict]:
    rows, s = [], 1_500_000_000_000
    while True:
        req = urllib.request.Request(URL.format(s=s), headers={"User-Agent": "Mozilla/5.0"})
        data = json.load(urllib.request.urlopen(req, timeout=40))
        if not data:
            break
        rows += data
        if len(data) < 1000:
            break
        s = data[-1][0] + 86_400_000
    return [{"ts": k[0] // 1000, "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
             "c": float(k[4]), "v": float(k[5])} for k in rows]


def _rsi(cl, i, p=14):
    g = l = 0.0
    for a, b in zip(cl[i - p:i], cl[i - p + 1:i + 1]):
        d = b - a
        g += max(d, 0)
        l += max(-d, 0)
    return 100.0 if l == 0 else 100 - 100 / (1 + (g / p) / (l / p))


def analyze(rows: list[dict] | None = None) -> dict | None:
    rows = rows or fetch_daily()
    n = len(rows)
    if n < 260:
        return None
    cl = [r["c"] for r in rows]
    hi = [r["h"] for r in rows]
    lo = [r["l"] for r in rows]
    i = n - 1
    ma = lambda j, k: sum(cl[j - k + 1:j + 1]) / k
    px = cl[i]
    h90, l90 = max(hi[i - 89:i + 1]), min(lo[i - 89:i + 1])
    ath_i = max(range(n), key=lambda j: hi[j])
    atr = sum(max(hi[j] - lo[j], abs(hi[j] - cl[j - 1]), abs(lo[j] - cl[j - 1]))
              for j in range(i - 13, i + 1)) / 14
    st = {
        "price": px, "ts": rows[i]["ts"],
        "ret": {k: px / cl[i - k] - 1 for k in (7, 14, 30, 90)},
        "ma": {k: ma(i, k) for k in (20, 50, 100, 200)},
        "ath": hi[ath_i], "ath_ts": rows[ath_i]["ts"],
        "h90": h90, "l90": l90, "pos90": (px - l90) / (h90 - l90),
        "rsi": _rsi(cl, i), "atr": atr,
        "ma200_slope": ma(i, 200) / ma(i - 20, 200) - 1,
        "ma50_slope": ma(i, 50) / ma(i - 10, 50) - 1,
    }
    cur = dict(rsi=st["rsi"], d50=px / st["ma"][50] - 1, dd=px / h90 - 1,
               a200=px > st["ma"][200], b20=px < st["ma"][20], b50=px < st["ma"][50])
    ev, last = [], -99
    for j in range(210, n - 30):
        m50, h = ma(j, 50), max(hi[j - 89:j + 1])
        if (abs(_rsi(cl, j) - cur["rsi"]) <= 7 and abs(cl[j] / m50 - 1 - cur["d50"]) <= 0.06
                and abs(cl[j] / h - 1 - cur["dd"]) <= 0.08 and (cl[j] > ma(j, 200)) == cur["a200"]
                and (cl[j] < ma(j, 20)) == cur["b20"] and (cl[j] < m50) == cur["b50"]):
            if j - last >= 10:
                ev.append({"ts": rows[j]["ts"], "fwd": cl[j + 30] / cl[j] - 1,
                           "worst": min(lo[j + 1:j + 31]) / cl[j] - 1,
                           "best": max(hi[j + 1:j + 31]) / cl[j] - 1})
            last = j
    base = [cl[j + 30] / cl[j] - 1 for j in range(210, n - 30)]
    st["analog"] = {
        "events": ev, "n": len(ev),
        "med": statistics.median(e["fwd"] for e in ev) if ev else None,
        "up": sum(1 for e in ev if e["fwd"] > 0),
        "med_worst": statistics.median(e["worst"] for e in ev) if ev else None,
        "med_best": statistics.median(e["best"] for e in ev) if ev else None,
        "base_med": statistics.median(base), "base_up": sum(1 for x in base if x > 0) / len(base),
    }
    return st
