"""Routescan - nguon on-chain du phong thu hai. KHONG CAN API KEY.

Da kiem chung: 10 request lien tiep deu 200, khong bi chan gat nhu Blockscout/Ethplorer.
Tra ve day du logIndex + timestamp + tokenDecimals nen ghep thang vao bang transfers duoc.

CANH BAO da kiem chung: tham so `tokenAddress` KHONG loc that su - phan hoi van lan
token khac (thay ca USDT va cac ban ghi amount=0). Bat buoc phai loc lai phia client
theo tokenAddress, neu khong se nap nham du lieu token khac vao so sach.
"""
from __future__ import annotations

from datetime import datetime

BASE = "https://api.routescan.io/v2/network/mainnet/evm/1"
RATE = 3.0
PAGE = 100


def _ts(value) -> int | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


class Routescan:
    def __init__(self, client, base: str = BASE):
        self.client = client
        self.base = base.rstrip("/")

    def erc20_transfers(self, addr: str, token: str, max_pages: int = 25,
                        stop_before_ts: int | None = None) -> list[dict]:
        """Lenh chuyen `token` cua `addr`, moi nhat truoc.

        stop_before_ts: dung khi da lui qua moc nay (chi can phan moi thi khong keo het lich su).
        """
        token = token.lower()
        out: list[dict] = []
        params = {"limit": PAGE}
        url = f"{self.base}/address/{addr}/erc20-transfers"

        for _ in range(max_pages):
            data = self.client.get_json(url, rate=RATE, params=params, allow_status={404})
            if not isinstance(data, dict):
                break
            items = data.get("items") or []
            if not items:
                break

            oldest = None
            for o in items:
                ts = _ts(o.get("timestamp"))
                oldest = ts if oldest is None else min(oldest, ts or oldest)
                # LOC PHIA CLIENT - bat buoc, xem docstring
                if (o.get("tokenAddress") or "").lower() != token:
                    continue
                raw = str(o.get("amount") or "0")
                if raw in ("0", ""):
                    continue
                src, dst = (o.get("from") or "").lower(), (o.get("to") or "").lower()
                if not (src and dst):
                    continue
                out.append({
                    "tx_hash": (o.get("txHash") or "").lower(),
                    "log_index": int(o.get("logIndex") or 0),
                    "block_number": int(o.get("blockNumber") or 0),
                    "ts": ts,
                    "from": src, "to": dst,
                    "raw_value": raw,
                    "decimals": int(o.get("tokenDecimals") or 18),
                })

            if stop_before_ts and oldest and oldest < stop_before_ts:
                break
            nxt = (data.get("link") or {}).get("nextToken") or data.get("nextToken")
            if not nxt:
                break
            params = {"limit": PAGE, "next": nxt}
        return out
