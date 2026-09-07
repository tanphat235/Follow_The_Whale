"""Blockscout - nguon on-chain chinh. KHONG CAN API KEY.

Da kiem chung thuc te tren eth.blockscout.com:
  - module=logs&action=getLogs theo block-range: OK, TRAN 1000 record/trang
  - /api/v2/addresses/{a}/token-transfers: OK, phan trang bang next_page_params
  - /api/v2/addresses/{a}: tra san is_contract, name, public_tags, is_scam
  - 12 request lien tiep deu 200 -> khong bi chan gat, van throttle 5 rps cho lich su

Vi sao khong dung RPC public: da test publicnode / 1rpc / drpc / llamarpc / flashbots,
tat ca deu fail cho du lieu lich su (doi token archive, gioi han 50 block, hoac khong co log index).
"""
from __future__ import annotations

from typing import Callable, Iterator

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
PAGE_CAP = 1000  # tran cua Blockscout cho getLogs


def _topic_addr(topic: str | None) -> str | None:
    """topic la 32 byte, dia chi nam o 20 byte cuoi."""
    if not topic or len(topic) < 42:
        return None
    return "0x" + topic[-40:].lower()


class Blockscout:
    def __init__(self, client, base: str = "https://eth.blockscout.com", verbose: bool = True):
        self.client = client
        self.base = base.rstrip("/")
        self.verbose = verbose

    # ---------- logs ----------

    def get_logs(self, address: str, from_block: int, to_block: int,
                 topic0: str = TRANSFER_TOPIC) -> list[dict]:
        data = self.client.get_json(
            f"{self.base}/api",
            params={
                "module": "logs", "action": "getLogs",
                "address": address, "topic0": topic0,
                "fromBlock": from_block, "toBlock": to_block,
            },
        )
        result = data.get("result")
        if not isinstance(result, list):
            msg = str(data.get("message") or data.get("result") or "")
            if "No logs found" in msg or "not found" in msg.lower():
                return []
            raise RuntimeError(f"getLogs loi [{from_block}-{to_block}]: {msg[:200]}")
        return result

    def sweep(self, address: str, from_block: int, to_block: int,
              chunk: int = 300,
              on_truncated: Callable[[int], None] | None = None
              ) -> Iterator[list[dict]]:
        """Quet [from_block, to_block] va dam bao KHONG mat log.

        Blockscout tra toi da 1000 record moi lan goi va BO QUA tham so page/offset
        (da kiem chung: page=1 va page=2 tra ket qua y het). Vi vay cham tran = co
        the con log bi cat, phai chia nho range.

        Ba tang xu ly:
          1. Chia doi range cho den khi con 1 block
          2. 1 block van tran -> di theo chieu transaction (endpoint co cursor that)
          3. Van khong xong -> bao cao qua on_truncated, KHONG am tham bo qua

        Chunk tu dieu chinh theo mat do thuc te (do duoc 0.5-5.8 log/block tuy giai doan),
        nham ~600 log/lan goi de it lang phi request nhat.
        """
        cursor = from_block
        size = max(1, chunk)
        while cursor <= to_block:
            stack = [(cursor, min(to_block, cursor + size - 1))]
            top = stack[0][1]
            while stack:
                lo, hi = stack.pop()
                logs = self.get_logs(address, lo, hi)
                n = len(logs)

                if n >= PAGE_CAP:
                    if hi > lo:
                        mid = (lo + hi) // 2
                        stack.append((mid + 1, hi))
                        stack.append((lo, mid))       # pop -> nua duoi truoc, giu thu tu tang
                        continue
                    # 1 block don le van tran -> vong qua duong transaction
                    if self.verbose:
                        print(f"    [dac] block {lo} co >={PAGE_CAP} log, di theo chieu tx", flush=True)
                    recovered = self.block_transfers_via_tx(lo, address)
                    if recovered is None:
                        if on_truncated:
                            on_truncated(lo)
                    else:
                        yield recovered
                    continue

                if logs:
                    yield self._parse_logs(logs)

                # Tu chinh chunk cho vong sau: nham ~600 log/lan goi.
                span = hi - lo + 1
                if span == size:
                    density = n / span if span else 0
                    if density > 0:
                        size = max(25, min(4000, int(600 / density)))
                    elif n == 0:
                        size = min(4000, size * 2)
            cursor = top + 1

    # ---------- duong vong khi 1 block tran ----------

    def block_tx_hashes(self, block_number: int, max_pages: int = 40) -> list[str] | None:
        return self._paged_hashes(
            f"{self.base}/api/v2/blocks/{block_number}/transactions", {}, max_pages)

    def _paged_hashes(self, url: str, params: dict, max_pages: int) -> list[str] | None:
        out, cur = [], dict(params)
        for _ in range(max_pages):
            data = self.client.get_json(url, params=cur, allow_status={404})
            if not data or not isinstance(data, dict):
                return None
            for item in data.get("items") or []:
                h = item.get("hash")
                if h:
                    out.append(h.lower())
            nxt = data.get("next_page_params")
            if not nxt:
                return out
            cur = {**params, **nxt}
        return None  # het so trang cho phep -> coi nhu that bai, khong bia du lieu

    def tx_token_transfers(self, tx_hash: str, token: str, max_pages: int = 60) -> list[dict] | None:
        out, cur = [], {"type": "ERC-20"}
        url = f"{self.base}/api/v2/transactions/{tx_hash}/token-transfers"
        for _ in range(max_pages):
            data = self.client.get_json(url, params=cur, allow_status={404})
            if not data or not isinstance(data, dict):
                return out
            for item in data.get("items") or []:
                parsed = self._parse_v2_transfer(item, token)
                if parsed:
                    out.append(parsed)
            nxt = data.get("next_page_params")
            if not nxt:
                return out
            cur = {"type": "ERC-20", **nxt}
        return None

    def block_transfers_via_tx(self, block_number: int, token: str) -> list[dict] | None:
        hashes = self.block_tx_hashes(block_number)
        if hashes is None:
            return None
        out: list[dict] = []
        for tx in hashes:
            got = self.tx_token_transfers(tx, token)
            if got is None:
                return None
            out.extend(got)
        for t in out:
            t.setdefault("block_number", block_number)
        return out

    @staticmethod
    def _parse_logs(logs: list[dict]) -> list[dict]:
        out = []
        for log in logs:
            topics = log.get("topics") or []
            if len(topics) < 3:
                continue  # Transfer chuan luon co 3 topic; it hon la event khac
            src = _topic_addr(topics[1])
            dst = _topic_addr(topics[2])
            if not src or not dst:
                continue
            raw = log.get("data") or "0x0"
            try:
                value = int(raw, 16) if isinstance(raw, str) else int(raw)
            except ValueError:
                continue
            out.append({
                "tx_hash": (log.get("transactionHash") or "").lower(),
                "log_index": _hexint(log.get("logIndex")),
                "block_number": _hexint(log.get("blockNumber")),
                "ts": _hexint(log.get("timeStamp")),
                "from": src, "to": dst, "raw_value": str(value),
            })
        return out

    # ---------- dia chi ----------

    def address_info(self, addr: str) -> dict | None:
        data = self.client.get_json(
            f"{self.base}/api/v2/addresses/{addr}", allow_status={404}
        )
        if not data or not isinstance(data, dict) or "hash" not in data:
            return None
        tags = [t.get("display_name") or t.get("label")
                for t in (data.get("public_tags") or []) if isinstance(t, dict)]
        return {
            "addr": addr.lower(),
            "is_contract": bool(data.get("is_contract")),
            "name": data.get("name") or (tags[0] if tags else None),
            "is_scam": bool(data.get("is_scam")),
            "tags": [t for t in tags if t],
        }

    def token_transfers(self, addr: str, token: str, max_pages: int = 60) -> list[dict]:
        """Toan bo lich su chuyen 1 token cua 1 dia chi (ca 2 chieu), moi nhat truoc."""
        out: list[dict] = []
        params: dict = {"type": "ERC-20", "token": token}
        url = f"{self.base}/api/v2/addresses/{addr}/token-transfers"
        for _ in range(max_pages):
            data = self.client.get_json(url, params=params, allow_status={404})
            if not data or not isinstance(data, dict):
                break
            for item in data.get("items") or []:
                parsed = self._parse_v2_transfer(item, token)
                if parsed:
                    out.append(parsed)
            nxt = data.get("next_page_params")
            if not nxt:
                break
            params = {"type": "ERC-20", "token": token, **nxt}
        return out

    @staticmethod
    def _parse_v2_transfer(item: dict, token: str) -> dict | None:
        tok = (item.get("token") or {}).get("address_hash") or (item.get("token") or {}).get("address")
        if tok and tok.lower() != token.lower():
            return None
        total = item.get("total") or {}
        raw = total.get("value")
        if raw is None:
            return None
        src = (item.get("from") or {}).get("hash")
        dst = (item.get("to") or {}).get("hash")
        if not src or not dst:
            return None
        return {
            "tx_hash": (item.get("transaction_hash") or item.get("tx_hash") or "").lower(),
            "log_index": int(item.get("log_index") or 0),
            "block_number": int(item.get("block_number") or 0),
            "ts": _iso_ts(item.get("timestamp")),
            "from": src.lower(), "to": dst.lower(), "raw_value": str(raw),
            "from_is_contract": (item.get("from") or {}).get("is_contract"),
            "to_is_contract": (item.get("to") or {}).get("is_contract"),
            "from_name": (item.get("from") or {}).get("name"),
            "to_name": (item.get("to") or {}).get("name"),
        }

    # ---------- block ----------

    def block_ts(self, number: int) -> int | None:
        data = self.client.get_json(
            f"{self.base}/api/v2/blocks/{number}", allow_status={404}
        )
        if not data or not isinstance(data, dict):
            return None
        return _iso_ts(data.get("timestamp"))

    def latest_block(self) -> int:
        data = self.client.get_json(
            f"{self.base}/api", params={"module": "block", "action": "eth_block_number"}
        )
        return _hexint(data.get("result")) or 0

    def block_by_time(self, ts: int, closest: str = "before") -> int | None:
        data = self.client.get_json(
            f"{self.base}/api",
            params={"module": "block", "action": "getblocknobytime",
                    "timestamp": int(ts), "closest": closest},
        )
        result = data.get("result")
        if isinstance(result, dict):
            result = result.get("blockNumber")
        try:
            return int(result)
        except (TypeError, ValueError):
            return None


def _hexint(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 16) if text.startswith("0x") else int(text)
    except ValueError:
        return None


def _iso_ts(value) -> int | None:
    """'2026-08-17T04:00:59.000000Z' -> epoch giay."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    from datetime import datetime
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None
