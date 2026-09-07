"""Ethplorer - nguon DU PHONG khi Blockscout can han ngach. KHONG CAN DANG KY.

Ethplorer cho phep dung apiKey=freekey cong khai, khong can tao tai khoan.
Da kiem chung: getTokenInfo / getAddressHistory / getAddressInfo deu chay.

Vi sao can nguon nay: Blockscout gioi han theo IP theo ngay. Sau ~5,000 request
no tra 429 cho MOI toc do, ke ca 0.25 req/s - do la can han ngach chu khong phai
qua nhanh, nen ha toc do khong cuu duoc. Luc do phai doi nguon.

Diem manh cua Ethplorer o day: no truy theo TUNG VI, nen de tra loi cau hoi
"cac vi trong watchlist da lam gi" ta chi ton ~1 request/vi thay vi quet ca chuoi khoi.
Diem yeu: khong quet duoc toan bo thi truong theo block range.
"""
from __future__ import annotations

BASE = "https://api.ethplorer.io"
FREEKEY = "freekey"
RATE = 1.0   # freekey rat de bi chan; 1 req/s la muc an toan


class Ethplorer:
    def __init__(self, client, api_key: str = FREEKEY, base: str = BASE):
        self.client = client
        self.key = api_key or FREEKEY
        self.base = base.rstrip("/")

    def _get(self, path: str, **params):
        data = self.client.get_json(f"{self.base}{path}", rate=RATE,
                                    params={"apiKey": self.key, **params})
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            raise RuntimeError(f"Ethplorer: {err.get('message', err)}")
        return data

    def address_history(self, addr: str, token: str, limit: int = 1000) -> list[dict]:
        """Toan bo lenh chuyen `token` cua `addr`, moi nhat truoc."""
        data = self._get(f"/getAddressHistory/{addr}", token=token.lower(),
                         type="transfer", limit=limit)
        out = []
        for o in (data or {}).get("operations", []):
            src, dst = (o.get("from") or "").lower(), (o.get("to") or "").lower()
            if not (src and dst):
                continue
            out.append({
                "tx_hash": (o.get("transactionHash") or "").lower(),
                "ts": int(o.get("timestamp") or 0) or None,
                "from": src, "to": dst,
                "raw_value": str(o.get("value") or "0"),
            })
        return out

    def token_balance(self, addr: str, token: str) -> float | None:
        """So du token thuc te on-chain - dung de doi chieu voi so du tinh tu so sach."""
        data = self._get(f"/getAddressInfo/{addr}", token=token.lower())
        for t in (data or {}).get("tokens", []) or []:
            info = t.get("tokenInfo") or {}
            if (info.get("address") or "").lower() == token.lower():
                try:
                    dec = int(info.get("decimals") or 18)
                    return int(t.get("rawBalance") or 0) / (10 ** dec)
                except (TypeError, ValueError):
                    return None
        return 0.0
