"""Etherscan API v2 - TUY CHON, chi dung khi ban co API key free.

Tool chay hoan toan khong can file nay: Blockscout keyless da du.
Dung den no khi:
  - Blockscout sap hoac cham
  - Ban muon doi chieu cheo so lieu tu 2 nguon doc lap

Han muc free: 5 call/giay, 100,000 call/ngay. Tu 07/2026 Etherscan ha tran
ban ghi moi request tu 10,000 xuong 1,000 - code duoi day da tinh theo 1,000.
Lay key mien phi tai https://etherscan.io/myapikey roi dat WT_ETHERSCAN_KEY.
"""
from __future__ import annotations

PAGE = 1000  # tran ban ghi/request cua goi free tu 07/2026


class Etherscan:
    def __init__(self, client, api_key: str,
                 base: str = "https://api.etherscan.io/v2/api", chain_id: int = 1):
        if not api_key:
            raise ValueError("Etherscan can API key. Dat WT_ETHERSCAN_KEY hoac bo qua module nay.")
        self.client = client
        self.key = api_key
        self.base = base
        self.chain_id = chain_id

    def _call(self, params: dict):
        data = self.client.get_json(self.base, rate=4.0, params={
            "chainid": self.chain_id, "apikey": self.key, **params})
        status, result = str(data.get("status", "")), data.get("result")
        if status == "1":
            return result
        msg = str(data.get("message") or result or "")
        if "No transactions found" in msg or "No records found" in msg:
            return []
        raise RuntimeError(f"Etherscan: {msg[:200]}")

    def token_transfers(self, address: str, contract: str,
                        start_block: int = 0, end_block: int = 99_999_999,
                        max_pages: int = 50) -> list[dict]:
        """Toan bo lich su chuyen 1 token cua 1 dia chi."""
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            rows = self._call({
                "module": "account", "action": "tokentx",
                "address": address, "contractaddress": contract,
                "startblock": start_block, "endblock": end_block,
                "page": page, "offset": PAGE, "sort": "asc",
            })
            if not rows:
                break
            out.extend(self._norm(r) for r in rows)
            if len(rows) < PAGE:
                break
        return out

    def logs(self, contract: str, topic0: str, from_block: int, to_block: int) -> list[dict]:
        rows = self._call({
            "module": "logs", "action": "getLogs", "address": contract, "topic0": topic0,
            "fromBlock": from_block, "toBlock": to_block, "page": 1, "offset": PAGE,
        })
        return rows or []

    @staticmethod
    def _norm(r: dict) -> dict:
        return {
            "tx_hash": (r.get("hash") or "").lower(),
            "log_index": int(r.get("logIndex") or 0),
            "block_number": int(r.get("blockNumber") or 0),
            "ts": int(r.get("timeStamp") or 0) or None,
            "from": (r.get("from") or "").lower(),
            "to": (r.get("to") or "").lower(),
            "raw_value": str(r.get("value") or "0"),
        }


def maybe(cfg, client) -> "Etherscan | None":
    """Tra ve client Etherscan neu co key trong config, khong thi None."""
    key = (cfg.get("sources", {}) or {}).get("etherscan_key") or ""
    if not key:
        return None
    return Etherscan(client, key,
                     cfg["sources"].get("etherscan_base", "https://api.etherscan.io/v2/api"),
                     int(cfg["sources"].get("etherscan_chainid", 1)))
