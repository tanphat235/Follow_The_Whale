"""Tim vung khang cu / ho tro va do muc do cang cua gia.

Ba phuong phap doc lap, deu tinh tu du lieu that:
  1. Volume profile - noi co nhieu hang da sang tay thi noi do can manh.
     Ly do: nguoi mua o vung do dang ket, ho ban ra khi gia ve hoa von.
  2. Dinh/day dao chieu - noi gia da tung quay dau.
  3. Thong ke lich su - khi gia vuot MA20 qua xa thi sau do thuong xay ra gi.

Phuong phap 3 quan trong nhat vi no tra loi bang TAN SUAT thay vi bang y kien.
Ket qua do duoc cho UNI: phan bo LUONG CUC - hoac bung no tiep, hoac la dinh cuc bo,
gan nhu khong co truong hop o giua. Khong the du doan huong, nhung gan nhu chac chan
se co mot nhip sut trong 30 ngay sau do.
"""
from __future__ import annotations

import statistics

from . import db

BINANCE_DAILY = "https://api.binance.com/api/v3/klines"


def fetch_daily(client, pair: str, conn, since_ms: int = 1_600_000_000_000) -> int:
    """Keo nen NGAY dai han ve bang prices_daily (chi can chay lai khi thieu)."""
    have = conn.execute("SELECT MAX(ts) mx, COUNT(*) n FROM prices_daily").fetchone()
    start = ((have["mx"] + 86400) * 1000) if have["mx"] else since_ms
    rows, added = [], 0
    while True:
        data = client.get_json(BINANCE_DAILY, rate=8.0, params={
            "symbol": pair, "interval": "1d", "startTime": start, "limit": 1000})
        if not isinstance(data, list) or not data:
            break
        rows = [(int(k[0]) // 1000, float(k[1]), float(k[2]), float(k[3]),
                 float(k[4]), float(k[5]), "binance") for k in data]
        db.upsert_prices_daily(conn, rows)
        added += len(rows)
        if len(data) < 1000:
            break
        start = int(data[-1][0]) + 86400000
    conn.commit()
    return added


def _daily(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT ts, open, high, low, close, volume FROM prices_daily ORDER BY ts")]


def volume_profile(conn, price_now: float, bucket: float = 0.5, top: int = 8) -> list[dict]:
    """Cac vung gia co nhieu hang da sang tay nhat, NAM TREN gia hien tai."""
    rows = _daily(conn)
    if not rows:
        return []
    buckets: dict[float, float] = {}
    for r in rows:
        mid = (r["high"] + r["low"]) / 2
        key = round(mid / bucket) * bucket
        buckets[key] = buckets.get(key, 0.0) + r["volume"]
    total = sum(buckets.values()) or 1.0
    above = [(k, v) for k, v in buckets.items() if k > price_now]
    above.sort(key=lambda x: -x[1])
    peak = above[0][1] if above else 1.0
    return [{"lo": k, "hi": k + bucket, "volume": v,
             "pct": v / total, "strength": v / peak,
             "gap": k / price_now - 1} for k, v in above[:top]]


def swing_levels(conn, price_now: float, window: int = 15, merge: float = 0.06) -> dict:
    """Dinh dao chieu TREN gia va day dao chieu DUOI gia."""
    rows = _daily(conn)
    if len(rows) < window * 2 + 1:
        return {"highs": [], "lows": []}

    def pick(key, cmp_fn, above: bool):
        found = []
        for i in range(window, len(rows) - window):
            val = rows[i][key]
            span = [r[key] for r in rows[i - window:i + window + 1]]
            if val == cmp_fn(span):
                found.append({"ts": rows[i]["ts"], "price": val})
        found = [f for f in found if (f["price"] > price_now) == above]
        found.sort(key=lambda f: -f["price"] if above else f["price"])
        kept = []
        for f in found:
            if not any(abs(f["price"] / k["price"] - 1) < merge for k in kept):
                f["gap"] = f["price"] / price_now - 1
                kept.append(f)
        return sorted(kept, key=lambda f: f["price"])

    return {"highs": pick("high", max, True), "lows": pick("low", min, False)[::-1]}


def stretch_study(conn, threshold: float = 0.40, horizon: int = 30) -> dict | None:
    """Lich su: moi khi gia vuot MA20 qua `threshold`, sau do xay ra gi?

    Chi lay lan DAU cua moi dot (khong dem lap lai tung ngay), neu khong mot dot
    keo dai 10 ngay se bi dem thanh 10 su kien va lam lech thong ke.
    """
    rows = _daily(conn)
    cl = [r["close"] for r in rows]
    if len(cl) < 60:
        return None

    events = []
    for i in range(20, len(cl) - horizon):
        ma = sum(cl[i - 19:i + 1]) / 20
        gap = cl[i] / ma - 1
        prev_gap = cl[i - 1] / (sum(cl[i - 20:i]) / 20) - 1
        if gap >= threshold > prev_gap:
            fwd = cl[i + horizon] / cl[i] - 1
            window = cl[i:i + horizon]
            events.append({"ts": rows[i]["ts"], "gap": gap,
                           "fwd7": cl[i + 7] / cl[i] - 1, "fwd30": fwd,
                           "best": max(window) / cl[i] - 1,
                           "worst": min(window) / cl[i] - 1})
    if not events:
        return None
    return {
        "n": len(events), "events": events,
        "med_fwd7": statistics.median(e["fwd7"] for e in events),
        "med_fwd30": statistics.median(e["fwd30"] for e in events),
        "n_up": sum(1 for e in events if e["fwd30"] > 0),
        "med_worst": statistics.median(e["worst"] for e in events),
        "med_best": statistics.median(e["best"] for e in events),
    }


def current_stretch(conn, price_now: float) -> dict:
    rows = _daily(conn)
    cl = [r["close"] for r in rows]
    out = {"price": price_now}
    if len(cl) >= 20:
        out["ma20"] = sum(cl[-20:]) / 20
        out["gap20"] = price_now / out["ma20"] - 1
    if len(cl) >= 50:
        out["ma50"] = sum(cl[-50:]) / 50
        out["gap50"] = price_now / out["ma50"] - 1
    return out
