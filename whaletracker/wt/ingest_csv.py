"""Buoc 0 - nap 25 file CSV Etherscan lam SEED.

CSV chi dung de: (1) lay danh sach dia chi ung vien, (2) lay nametag co san.
KHONG dung cot "Value (USD)" cua Etherscan: da kiem chung gia ngam (Value/Amount)
phang li o 3.26-3.27$ suot ca 7157 block -> do la gia LUC EXPORT chu khong phai
gia tai block. Gia that lay tu Binance o buoc backfill.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from . import config, db

# Nametag Etherscan hay co dang "Binance Dep: 0x86a067...6d4c63" hoac " Binance 14" (co space dau).
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _num(text: str) -> float:
    """'1,831.787829' -> 1831.787829"""
    cleaned = (text or "").replace(",", "").replace("$", "").strip()
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _addr(text: str) -> str:
    return (text or "").strip().lower()


def run(cfg: config.Config, conn, csv_dir: Path | None = None, verbose: bool = True) -> dict:
    directory = Path(csv_dir) if csv_dir else config.CSV_DIR
    files = sorted(directory.glob("*.csv"))
    if not files:
        raise SystemExit(f"[ingest] khong thay file CSV nao trong {directory}")

    conn.execute("DELETE FROM csv_transfers")
    conn.execute("DELETE FROM labels WHERE source='csv'")

    rows: list[tuple] = []
    addrs: set[str] = set()
    tags: dict[str, str] = {}
    blocks: list[tuple[int, int]] = []
    skipped = 0

    for path in files:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for rec in csv.DictReader(fh):
                src = _addr(rec.get("From", ""))
                dst = _addr(rec.get("To", ""))
                if not (_ADDR_RE.match(src) and _ADDR_RE.match(dst)):
                    skipped += 1
                    continue
                tx = (rec.get("Transaction Hash") or "").strip().lower()
                blk_raw = (rec.get("Block") or "").replace(",", "").strip()
                blk = int(blk_raw) if blk_raw.isdigit() else None
                amount = _num(rec.get("Amount", ""))
                # "Method " co dau cach thua o cuoi trong header goc cua Etherscan.
                method = (rec.get("Method ") or rec.get("Method") or "").strip()

                rows.append((tx, blk, src, dst, amount, method, path.name))
                addrs.update((src, dst))
                for addr_key, tag_key in ((src, "From_NameTag"), (dst, "To_NameTag")):
                    tag = (rec.get(tag_key) or "").strip()
                    if tag:
                        tags[addr_key] = tag

    conn.executemany(
        "INSERT INTO csv_transfers(tx_hash,block_number,from_addr,to_addr,amount,method,src_file)"
        " VALUES(?,?,?,?,?,?,?)",
        rows,
    )
    db.touch_addresses(conn, addrs)
    for addr, tag in tags.items():
        db.add_label(conn, addr, tag, "csv")
        conn.execute(
            "UPDATE addresses SET name=COALESCE(name,?) WHERE addr=?", (tag, addr)
        )

    block_nums = [r[1] for r in rows if r[1]]
    db.meta_set(conn, "csv_files", len(files))
    db.meta_set(conn, "csv_rows", len(rows))
    if block_nums:
        db.meta_set(conn, "csv_block_min", min(block_nums))
        db.meta_set(conn, "csv_block_max", max(block_nums))
    conn.commit()

    stats = {
        "files": len(files),
        "rows": len(rows),
        "skipped": skipped,
        "addresses": len(addrs),
        "tagged": len(tags),
        "untagged": len(addrs) - len(tags),
        "block_min": min(block_nums) if block_nums else None,
        "block_max": max(block_nums) if block_nums else None,
    }

    if verbose:
        print(f"[ingest] {stats['files']} file -> {stats['rows']:,} transfer"
              + (f" (bo qua {skipped} dong hong)" if skipped else ""))
        print(f"[ingest] {stats['addresses']} dia chi: {stats['tagged']} co nametag, "
              f"{stats['untagged']} chua tag (whale that thuong nam o nhom chua tag)")
        print(f"[ingest] block {stats['block_min']:,} -> {stats['block_max']:,} "
              f"({stats['block_max'] - stats['block_min']:,} block ~ "
              f"{(stats['block_max'] - stats['block_min']) * 12 / 3600:.1f} gio)")

        # Moc kiem chung tu khao sat ban dau - lech la biet ngay parse sai.
        if stats["rows"] != 2491:
            print(f"[ingest] CANH BAO: mong doi 2,491 dong, nhan duoc {stats['rows']:,}")
        if stats["addresses"] != 635:
            print(f"[ingest] CANH BAO: mong doi 635 dia chi, nhan duoc {stats['addresses']}")

    return stats
