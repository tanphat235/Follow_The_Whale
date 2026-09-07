"""Nguon gia - nen 1 phut, KHONG CAN API KEY.

Chuoi du phong: Binance -> OKX -> Kraken -> CoinGecko.
Binance chan IP My (HTTP 451); neu deploy Oracle Cloud o region US thi cac nguon sau
se tu dong ganh. Van nen chon region Singapore/Tokyo/Frankfurt cho chac.

Da kiem chung Binance UNIUSDT tra dung: dinh 4.577 ngay 2026-07-31, day 2.316 ngay 2026-06-05.
"""
from __future__ import annotations

import bisect
import time
from typing import Callable

MINUTE = 60


class PriceSource:
    name = "base"
    rate = 8.0

    def fetch(self, client, pair: str, start_ts: int, end_ts: int) -> list[tuple]:
        raise NotImplementedError


class Binance(PriceSource):
    name = "binance"
    rate = 8.0
    URL = "https://api.binance.com/api/v3/klines"

    def fetch(self, client, pair, start_ts, end_ts):
        out, cursor = [], start_ts
        while cursor < end_ts:
            data = client.get_json(self.URL, rate=self.rate, params={
                "symbol": pair, "interval": "1m",
                "startTime": cursor * 1000, "endTime": end_ts * 1000, "limit": 1000,
            })
            if not isinstance(data, list) or not data:
                break
            for k in data:
                out.append((int(k[0]) // 1000, float(k[1]), float(k[2]),
                            float(k[3]), float(k[4]), float(k[5]), self.name))
            last = int(data[-1][0]) // 1000
            if last <= cursor:
                break
            cursor = last + MINUTE
            if len(data) < 1000:
                break
        return out


class OKX(PriceSource):
    name = "okx"
    rate = 4.0
    URL = "https://www.okx.com/api/v5/market/history-candles"

    def fetch(self, client, pair, start_ts, end_ts):
        inst = _to_dash(pair)
        out, cursor = [], end_ts  # OKX phan trang lui tu moi -> cu
        while cursor > start_ts:
            data = client.get_json(self.URL, rate=self.rate, params={
                "instId": inst, "bar": "1m", "after": cursor * 1000, "limit": 300,
            })
            rows = (data or {}).get("data") or []
            if not rows:
                break
            for k in rows:
                ts = int(k[0]) // 1000
                if ts < start_ts:
                    continue
                out.append((ts, float(k[1]), float(k[2]), float(k[3]),
                            float(k[4]), float(k[5]), self.name))
            oldest = min(int(k[0]) // 1000 for k in rows)
            if oldest >= cursor:
                break
            cursor = oldest
        return out


class Kraken(PriceSource):
    name = "kraken"
    rate = 1.0
    URL = "https://api.kraken.com/0/public/OHLC"

    def fetch(self, client, pair, start_ts, end_ts):
        base = pair.replace("USDT", "").replace("USD", "")
        data = client.get_json(self.URL, rate=self.rate, params={
            "pair": f"{base}USD", "interval": 1, "since": start_ts,
        })
        result = (data or {}).get("result") or {}
        rows = next((v for k, v in result.items() if k != "last"), [])
        return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]),
                 float(k[4]), float(k[6]), self.name)
                for k in rows if start_ts <= int(k[0]) <= end_ts]


class CoinGecko(PriceSource):
    """Cuu canh cuoi. Do phan giai tho hon (5 phut - 1 gio) nhung con hon khong co gi."""
    name = "coingecko"
    rate = 0.4
    URL = "https://api.coingecko.com/api/v3/coins/{id}/market_chart/range"
    IDS = {"UNIUSDT": "uniswap", "UNIUSD": "uniswap"}

    def fetch(self, client, pair, start_ts, end_ts):
        coin = self.IDS.get(pair.upper(), "uniswap")
        out = []
        cursor = start_ts
        while cursor < end_ts:  # cua so <=1 ngay de duoc do phan giai 5 phut
            stop = min(cursor + 86400, end_ts)
            data = client.get_json(self.URL.format(id=coin), rate=self.rate, params={
                "vs_currency": "usd", "from": cursor, "to": stop,
            })
            points = (data or {}).get("prices") or []
            for ms, price in points:
                ts = int(ms) // 1000
                out.append((ts, price, price, price, price, 0.0, self.name))
            cursor = stop
            if not points:
                break
        return out


CHAIN = [Binance(), OKX(), Kraken(), CoinGecko()]


def _to_dash(pair: str) -> str:
    for quote in ("USDT", "USDC", "USD"):
        if pair.upper().endswith(quote):
            return f"{pair[:-len(quote)].upper()}-{quote}"
    return pair.upper()


def fetch_range(client, pair: str, start_ts: int, end_ts: int,
                on_progress: Callable[[str, int], None] | None = None) -> list[tuple]:
    """Thu tung nguon cho den khi co du lieu. Tra ve list (ts,o,h,l,c,v,src)."""
    errors = []
    for source in CHAIN:
        try:
            rows = source.fetch(client, pair, start_ts, end_ts)
        except Exception as exc:
            errors.append(f"{source.name}: {type(exc).__name__} {str(exc)[:120]}")
            print(f"  [gia] {source.name} that bai ({type(exc).__name__}), thu nguon tiep theo",
                  flush=True)
            continue
        if rows:
            if on_progress:
                on_progress(source.name, len(rows))
            return rows
        errors.append(f"{source.name}: khong co du lieu")
    raise RuntimeError("Tat ca nguon gia deu that bai:\n  " + "\n  ".join(errors))


def spot(client, pair: str) -> float | None:
    """Gia hien tai, dung cho canh bao realtime."""
    try:
        data = client.get_json("https://api.binance.com/api/v3/ticker/price",
                               rate=8.0, params={"symbol": pair})
        if data and "price" in data:
            return float(data["price"])
    except Exception:
        pass
    try:
        data = client.get_json("https://www.okx.com/api/v5/market/ticker",
                               rate=4.0, params={"instId": _to_dash(pair)})
        rows = (data or {}).get("data") or []
        if rows:
            return float(rows[0]["last"])
    except Exception:
        pass
    return None


class PriceBook:
    """Tra gia tai thoi diem bat ky bang tim kiem nhi phan tren nen 1 phut da cache."""

    def __init__(self, conn, max_gap_sec: int = 3 * 3600):
        rows = conn.execute("SELECT ts, close, high, low FROM prices ORDER BY ts").fetchall()
        self.ts = [r["ts"] for r in rows]
        self.close = [r["close"] for r in rows]
        self.high = [r["high"] for r in rows]
        self.low = [r["low"] for r in rows]
        self.max_gap = max_gap_sec
        self._sorted_close = sorted(self.close) if self.close else []

    def __len__(self) -> int:
        return len(self.ts)

    @property
    def span(self) -> tuple[int, int] | None:
        return (self.ts[0], self.ts[-1]) if self.ts else None

    def at(self, ts: int | None) -> float | None:
        """Gia dong cua cua nen gan nhat. Tra None neu lech qua max_gap (khong doan bua)."""
        if ts is None or not self.ts:
            return None
        idx = bisect.bisect_left(self.ts, ts)
        best, best_gap = None, None
        for j in (idx - 1, idx):
            if 0 <= j < len(self.ts):
                gap = abs(self.ts[j] - ts)
                if best_gap is None or gap < best_gap:
                    best, best_gap = self.close[j], gap
        return best if best_gap is not None and best_gap <= self.max_gap else None

    def pctile(self, price: float | None) -> float | None:
        """Phan vi cua muc gia trong toan bo phan phoi gia cua cua so (0..1).

        Day chinh la thuoc do 'mua re hay mua dat' - 0.05 nghia la mua o vung
        re nhat 5% cua ca chu ky.
        """
        if price is None or not self._sorted_close:
            return None
        idx = bisect.bisect_left(self._sorted_close, price)
        return idx / len(self._sorted_close)

    def forward_return(self, ts: int | None, horizon_sec: int) -> float | None:
        """Loi nhuan gia sau `horizon_sec` ke tu ts. Nen backtest copy-trade."""
        if ts is None:
            return None
        now_price = self.at(ts)
        later = self.at(ts + horizon_sec)
        if not now_price or not later or now_price <= 0:
            return None
        if self.ts and ts + horizon_sec > self.ts[-1] + self.max_gap:
            return None  # chua du du lieu tuong lai -> khong bia
        return (later - now_price) / now_price

    def extreme(self, start_ts: int, end_ts: int) -> tuple[float, float]:
        lo = bisect.bisect_left(self.ts, start_ts)
        hi = bisect.bisect_right(self.ts, end_ts)
        if lo >= hi:
            return (0.0, 0.0)
        return (min(self.low[lo:hi]), max(self.high[lo:hi]))
