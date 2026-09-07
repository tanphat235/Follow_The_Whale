"""Buoc 1+2 - keo du lieu on-chain va gia ve SQLite.

Ba giai doan:
  bulk  : quet toan bo Transfer cua UNI trong cua so phan tich (Blockscout getLogs)
  deep  : voi ~200 vi ung vien, keo TOAN BO lich su UNI (ke ca truoc cua so) -> cost basis dung
  meta  : lay is_contract / name / is_scam cho cac vi ung vien

Tat ca deu resume duoc: moi chunk block hoan tat ghi 1 dong vao fetch_log,
chay lai se bo qua phan da xong.
"""
from __future__ import annotations

import time

from . import config, db, known
from .http import Client
from .sources import prices as price_src
from .sources.blockscout import Blockscout

# Chunk khoi diem; sau do tu dieu chinh theo mat do that (do duoc 0.5-5.8 log/block).
CHUNK = 300

# Nguong bui: transfer nho hon muc nay bi loai khoi phan tich.
# Ly do: da kiem chung block 25652817 co >1000 log UNI thi CA 1000 deu < 0.01 UNI,
# 333 trong so do gia tri dung bang 0 - do la log rac tu batch-swap cua Balancer Vault.
# Giu chung lai chi lam nhieu du lieu va phinh DB. So luong bi loai duoc DEM va BAO CAO.
DUST_UNI = 0.01


def _amount(raw: str, decimals: int) -> float:
    try:
        return int(raw) / (10 ** decimals)
    except (TypeError, ValueError):
        return 0.0


def _resolve_block(bs: Blockscout, conn, ts: int, closest: str, cache_key: str) -> int:
    cached = db.meta_get(conn, cache_key)
    if cached:
        return int(cached)
    blk = bs.block_by_time(ts, closest)
    if blk is None:
        raise SystemExit(f"[backfill] khong doi duoc timestamp {ts} sang block")
    db.meta_set(conn, cache_key, blk)
    conn.commit()
    return blk


# ---------------- giai doan 1: quet khoi luong lon ----------------

def bulk_logs(cfg, conn, client, bs, verbose=True, dry_run=False) -> dict:
    since_ts, until_ts = cfg.since_ts(), cfg.until_ts()
    start = _resolve_block(bs, conn, since_ts, "after", f"blk_since_{since_ts}")
    end = _resolve_block(bs, conn, until_ts, "before", f"blk_until_{until_ts}")
    total_blocks = end - start + 1
    n_chunks = (total_blocks + CHUNK - 1) // CHUNK

    done = {r["key"] for r in conn.execute(
        "SELECT key FROM fetch_log WHERE kind='bulk'").fetchall()}
    todo = [(s, min(end, s + CHUNK - 1))
            for s in range(start, end + 1, CHUNK)
            if f"{s}" not in done]

    if verbose:
        print(f"[bulk] cua so {config.fmt_ts(since_ts, False)} -> {config.fmt_ts(until_ts, False)}")
        print(f"[bulk] block {start:,} -> {end:,} ({total_blocks:,} block, {n_chunks:,} chunk)")
        print(f"[bulk] da xong {n_chunks - len(todo):,} chunk, con lai {len(todo):,}")
    if dry_run:
        return {"planned_calls": len(todo), "start": start, "end": end}

    n_new = n_dust = 0
    truncated: list[int] = []
    t0 = time.time()

    def note_truncated(block: int) -> None:
        truncated.append(block)
        conn.execute("INSERT OR REPLACE INTO truncated_blocks(block_number,noted_ts) VALUES(?,?)",
                     (block, int(time.time())))

    for i, (lo, hi) in enumerate(todo, 1):
        buf, blocks, addrs = [], {}, set()
        for batch in bs.sweep(cfg.contract, lo, hi, chunk=CHUNK, on_truncated=note_truncated):
            for t in batch:
                if not t["tx_hash"] or t["log_index"] is None:
                    continue
                amt = _amount(t["raw_value"], cfg.decimals)
                if amt < DUST_UNI:
                    n_dust += 1
                    continue
                buf.append((t["tx_hash"], t["log_index"], t["block_number"], t["ts"],
                            t["from"], t["to"], t["raw_value"], amt, "logs"))
                if t["ts"] and t["block_number"]:
                    blocks[t["block_number"]] = t["ts"]
                addrs.update((t["from"], t["to"]))
        if buf:
            db.upsert_transfers(conn, buf)
            db.upsert_blocks(conn, list(blocks.items()))
            db.touch_addresses(conn, addrs)
            n_new += len(buf)
        db.mark_fetched(conn, "bulk", str(lo))
        conn.commit()

        if verbose and (i % 20 == 0 or i == len(todo)):
            rate = i / max(time.time() - t0, 1e-9)
            eta = (len(todo) - i) / max(rate, 1e-9)
            print(f"  [bulk] {i:,}/{len(todo):,} chunk | {n_new:,} transfer | "
                  f"{rate:.2f} chunk/s | con ~{eta / 60:.1f} phut", flush=True)

    total = conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]
    if verbose:
        print(f"[bulk] xong. Tong {total:,} transfer trong DB (+{n_new:,} lan nay)")
        if n_dust:
            print(f"[bulk] da loc {n_dust:,} log bui (<{DUST_UNI} UNI) - log rac tu batch-swap, "
                  f"khong anh huong phan tich whale")
        if truncated:
            print(f"[bulk] CANH BAO: {len(truncated)} block khong lay du duoc log: "
                  f"{truncated[:5]}{'...' if len(truncated) > 5 else ''}")
    return {"new": n_new, "total": total, "dust": n_dust,
            "truncated": len(truncated), "start": start, "end": end}


# ---------------- giai doan 2: gia ----------------

def backfill_prices(cfg, conn, client, verbose=True, dry_run=False) -> dict:
    pair = cfg["token"]["price_pair"]
    since_ts, until_ts = cfg.since_ts(), cfg.until_ts()
    row = conn.execute("SELECT MIN(ts) lo, MAX(ts) hi, COUNT(*) n FROM prices").fetchone()
    have_lo, have_hi, have_n = row["lo"], row["hi"], row["n"]

    gaps = []
    if not have_n:
        gaps.append((since_ts, until_ts))
    else:
        if since_ts < have_lo - 120:
            gaps.append((since_ts, have_lo))
        if until_ts > have_hi + 120:
            gaps.append((have_hi, until_ts))

    if verbose:
        print(f"[gia] da co {have_n:,} nen 1m" + (
            f" ({config.fmt_ts(have_lo)} -> {config.fmt_ts(have_hi)})" if have_n else ""))
        print(f"[gia] can lap {len(gaps)} khoang trong")
    if dry_run or not gaps:
        return {"candles": have_n, "gaps": len(gaps)}

    added = 0
    for lo, hi in gaps:
        rows = price_src.fetch_range(
            client, pair, lo, hi,
            on_progress=lambda src, n: print(f"  [gia] {src}: {n:,} nen", flush=True) if verbose else None,
        )
        db.upsert_prices(conn, rows)
        conn.commit()
        added += len(rows)

    total = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    if verbose:
        print(f"[gia] xong. {total:,} nen (+{added:,})")
    return {"candles": total, "added": added}


# ---------------- giai doan 3: dao sau tung vi ----------------

def _is_infrastructure(conn, addr: str) -> bool:
    """Loai ha tang (CEX / pool / router / MEV / bridge) khoi danh sach dao sau.

    Sau khi quet ca cua so, top volume gan nhu toan la pool va vi CEX - dao sau
    lich su cua chung vua ton request vua vo nghia. Chua chay classify o thoi diem nay
    nen dua vao nametag da co (tu CSV) va co is_contract neu Blockscout da tra ve.
    """
    if addr in known.FIXED:
        return True
    row = conn.execute(
        "SELECT is_contract, name FROM addresses WHERE addr=?", (addr,)).fetchone()
    if row and row["is_contract"]:
        return True
    labels = [r["label"] for r in conn.execute(
        "SELECT label FROM labels WHERE addr=?", (addr,))]
    if row and row["name"]:
        labels.append(row["name"])
    return any(known.match_label(text) for text in labels)


def candidate_addresses(cfg, conn, top_n: int) -> list[str]:
    """Uu tien vi co dong tien lon nhat trong ngay dinh (data CSV cua ban),
    roi den vi co volume lon nhat trong toan cua so - da loai ha tang."""
    seed = conn.execute("""
        SELECT addr, SUM(vol) vol FROM (
            SELECT to_addr addr, SUM(amount) vol FROM csv_transfers GROUP BY to_addr
            UNION ALL
            SELECT from_addr addr, SUM(amount) vol FROM csv_transfers GROUP BY from_addr
        ) GROUP BY addr ORDER BY vol DESC LIMIT ?
    """, (top_n * 4,)).fetchall()

    broad = conn.execute("""
        SELECT addr, SUM(vol) vol FROM (
            SELECT to_addr addr, SUM(amount) vol FROM transfers GROUP BY to_addr
            UNION ALL
            SELECT from_addr addr, SUM(amount) vol FROM transfers GROUP BY from_addr
        ) GROUP BY addr ORDER BY vol DESC LIMIT ?
    """, (top_n * 4,)).fetchall()

    ordered, seen = [], set()
    for row in list(seed) + list(broad):
        addr = row["addr"]
        if addr in seen:
            continue
        seen.add(addr)
        if _is_infrastructure(conn, addr):
            continue
        ordered.append(addr)
        if len(ordered) >= top_n:
            break
    return ordered


def fetch_address_meta(cfg, conn, bs, addrs: list[str], verbose=True) -> int:
    todo = [a for a in addrs if not conn.execute(
        "SELECT meta_fetched FROM addresses WHERE addr=?", (a,)).fetchone()["meta_fetched"]]
    if verbose:
        print(f"[meta] can lay thong tin {len(todo):,}/{len(addrs):,} dia chi")
    for i, addr in enumerate(todo, 1):
        info = bs.address_info(addr)
        if info:
            conn.execute(
                "UPDATE addresses SET is_contract=?, name=COALESCE(name,?), is_scam=?,"
                " meta_fetched=1 WHERE addr=?",
                (int(info["is_contract"]), info["name"], int(info["is_scam"]), addr),
            )
            for tag in info["tags"]:
                db.add_label(conn, addr, tag, "blockscout")
        else:
            conn.execute("UPDATE addresses SET meta_fetched=1 WHERE addr=?", (addr,))
        if i % 25 == 0:
            conn.commit()
            if verbose:
                print(f"  [meta] {i:,}/{len(todo):,}", flush=True)
    conn.commit()
    return len(todo)


def deep_dive(cfg, conn, bs, addrs: list[str], verbose=True, dry_run=False) -> dict:
    todo = [a for a in addrs if not db.fetch_done(conn, "deep", a)]
    if verbose:
        print(f"[deep] dao sau {len(todo):,}/{len(addrs):,} vi (lay TOAN BO lich su UNI)")
    if dry_run:
        return {"planned": len(todo)}

    n_new = 0
    for i, addr in enumerate(todo, 1):
        try:
            items = bs.token_transfers(addr, cfg.contract)
        except Exception as exc:
            print(f"  [deep] bo qua {addr}: {type(exc).__name__} {str(exc)[:90]}", flush=True)
            continue
        buf, blocks, addrs_seen = [], {}, set()
        for t in items:
            if not t["tx_hash"]:
                continue
            buf.append((t["tx_hash"], t["log_index"], t["block_number"], t["ts"],
                        t["from"], t["to"], t["raw_value"],
                        _amount(t["raw_value"], cfg.decimals), "deep"))
            if t["ts"] and t["block_number"]:
                blocks[t["block_number"]] = t["ts"]
            addrs_seen.update((t["from"], t["to"]))
            # Blockscout dinh kem is_contract cua ca 2 phia - nhat luon, khoi goi rieng
            for side, flag, nm in (("from", "from_is_contract", "from_name"),
                                   ("to", "to_is_contract", "to_name")):
                if t.get(flag) is not None:
                    conn.execute(
                        "INSERT INTO addresses(addr,is_contract,name) VALUES(?,?,?)"
                        " ON CONFLICT(addr) DO UPDATE SET"
                        " is_contract=COALESCE(addresses.is_contract, excluded.is_contract),"
                        " name=COALESCE(addresses.name, excluded.name)",
                        (t[side], int(bool(t[flag])), t.get(nm)),
                    )
        if buf:
            db.upsert_transfers(conn, buf)
            db.upsert_blocks(conn, list(blocks.items()))
            db.touch_addresses(conn, addrs_seen)
            n_new += len(buf)
        conn.execute("UPDATE addresses SET deep_fetched=1 WHERE addr=?", (addr,))
        db.mark_fetched(conn, "deep", addr)
        conn.commit()
        if verbose and (i % 10 == 0 or i == len(todo)):
            print(f"  [deep] {i:,}/{len(todo):,} vi | {n_new:,} transfer", flush=True)
    return {"wallets": len(todo), "new": n_new}


# ---------------- dieu phoi ----------------

def backfill_missing_ts(conn, bs, verbose=True) -> int:
    """Vai transfer thieu timestamp -> lay tu bang blocks, con thieu thi goi API."""
    conn.execute("""
        UPDATE transfers SET ts = (SELECT b.ts FROM blocks b WHERE b.number = transfers.block_number)
        WHERE ts IS NULL AND EXISTS (SELECT 1 FROM blocks b WHERE b.number = transfers.block_number)
    """)
    conn.commit()
    missing = [r["block_number"] for r in conn.execute(
        "SELECT DISTINCT block_number FROM transfers WHERE ts IS NULL LIMIT 500").fetchall()]
    if not missing:
        return 0
    if verbose:
        print(f"[ts] con {len(missing)} block thieu timestamp, dang lay")
    for blk in missing:
        ts = bs.block_ts(blk)
        if ts:
            db.upsert_blocks(conn, [(blk, ts)])
            conn.execute("UPDATE transfers SET ts=? WHERE block_number=? AND ts IS NULL", (ts, blk))
    conn.commit()
    return len(missing)


def run(cfg, conn, *, dry_run=False, skip_deep=False, verbose=True) -> dict:
    client = Client(cfg, verbose=verbose)
    bs = Blockscout(client, cfg["sources"]["blockscout_base"], verbose=verbose)

    result = {}
    result["bulk"] = bulk_logs(cfg, conn, client, bs, verbose, dry_run)
    result["prices"] = backfill_prices(cfg, conn, client, verbose, dry_run)

    top_n = int(cfg["thresholds"]["deep_dive_top_n"])
    addrs = candidate_addresses(cfg, conn, top_n)
    if not dry_run:
        fetch_address_meta(cfg, conn, bs, addrs, verbose)
    if not skip_deep:
        result["deep"] = deep_dive(cfg, conn, bs, addrs, verbose, dry_run)
    if not dry_run:
        backfill_missing_ts(conn, bs, verbose)

    result["requests"] = client.n_requests
    if verbose:
        print(f"[backfill] tong cong {client.n_requests:,} request HTTP")
    return result
