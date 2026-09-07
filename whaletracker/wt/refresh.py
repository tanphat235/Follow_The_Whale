"""Cap nhat lich su cac vi dang theo doi, KHONG can quet toan bo chuoi khoi.

Dung khi chi can tra loi "cac vi da lam gi gan day" - re hon quet bulk rat nhieu:
~1 request cho moi vi, thay vi hang tram chunk block.

Nguon: Ethplorer (freekey, khong can dang ky). Xem wt/sources/ethplorer.py.
"""
from __future__ import annotations

import time

from . import config, db
from .http import Client
from .sources.ethplorer import Ethplorer
from .sources.routescan import Routescan


def pick_wallets(conn, cfg, top_n: int, min_score: float) -> list[str]:
    """Vi dang theo doi + vi co dong tien lon nhat, bo ha tang."""
    seen, out = set(), []
    for sql, args in (
        ("""SELECT wallet FROM wallet_stats
            WHERE klass IN ('whale_candidate','fund','retail') AND copy_score >= ?
            ORDER BY copy_score DESC""", (min_score,)),
        ("""SELECT wallet FROM wallet_stats
            WHERE klass IN ('whale_candidate','fund')
            ORDER BY gross_uni DESC LIMIT ?""", (top_n,)),
    ):
        for r in conn.execute(sql, args):
            if r["wallet"] not in seen:
                seen.add(r["wallet"])
                out.append(r["wallet"])
    return out[:top_n]


def run(cfg, conn, top_n: int = 150, verbose: bool = True) -> dict:
    client = Client(cfg, verbose=verbose)
    # Routescan la nguon chinh: da do 10 request lien tiep khong bi chan, con
    # Blockscout va Ethplorer deu tra 429 sau vai nghin request/ngay tren cung 1 IP.
    rs = Routescan(client)
    ep = Ethplorer(client)
    contract = cfg.contract
    dec = cfg.decimals

    wallets = pick_wallets(conn, cfg, top_n, float(cfg["monitor"]["min_score_to_watch"]))
    if verbose:
        print(f"[refresh] cap nhat {len(wallets)} vi qua Ethplorer (freekey, ~1 req/s)")

    n_new = n_fail = 0
    balances: dict[str, float] = {}
    t0 = time.time()

    for i, w in enumerate(wallets, 1):
        ops, err = None, None
        for name, fn in (("routescan", lambda: rs.erc20_transfers(w, contract)),
                         ("ethplorer", lambda: ep.address_history(w, contract))):
            try:
                ops = fn()
                break
            except Exception as exc:
                err = f"{name}: {type(exc).__name__} {str(exc)[:60]}"
        if ops is None:
            n_fail += 1
            if verbose:
                print(f"  [refresh] loi {w[:12]}..: {err}", flush=True)
            continue

        rows, blocks, addrs = [], {}, set()
        for o in ops:
            if not o["tx_hash"]:
                continue
            try:
                amt = int(o["raw_value"]) / (10 ** int(o.get("decimals") or dec))
            except (TypeError, ValueError):
                continue
            if amt < 0.01:      # bo bui, giong buoc backfill
                continue
            # Ethplorer khong tra log_index. Dung -1 lam cho danh dau "nguon ethplorer";
            # neu ban ghi that da co tu getLogs thi PRIMARY KEY khac nhau nen khong de len nhau,
            # va buoc dedupe ben duoi se loai ban trung.
            rows.append((o["tx_hash"], int(o.get("log_index", -1)),
                         int(o.get("block_number") or 0), o["ts"], o["from"], o["to"],
                         o["raw_value"], amt, "refresh"))
            addrs.update((o["from"], o["to"]))
        if rows:
            db.upsert_transfers(conn, rows)
            db.touch_addresses(conn, addrs)
            n_new += len(rows)
        conn.commit()

        if verbose and (i % 20 == 0 or i == len(wallets)):
            rate = i / max(time.time() - t0, 1e-9)
            print(f"  [refresh] {i}/{len(wallets)} vi | {n_new:,} ban ghi | "
                  f"con ~{(len(wallets) - i) / max(rate, 1e-9) / 60:.1f} phut", flush=True)

    # Bo ban ghi Ethplorer bi trung voi ban ghi getLogs da co (cung tx, cung 2 dau, cung so luong).
    dropped = conn.execute("""
        DELETE FROM transfers WHERE log_index = -1 AND EXISTS (
            SELECT 1 FROM transfers o
            WHERE o.tx_hash = transfers.tx_hash AND o.log_index >= 0
              AND o.from_addr = transfers.from_addr AND o.to_addr = transfers.to_addr
              AND ABS(o.amount - transfers.amount) < 1e-9)""").rowcount
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]
    if verbose:
        print(f"[refresh] +{n_new:,} ban ghi, bo {dropped:,} ban trung -> tong {total:,}")
        if n_fail:
            print(f"[refresh] {n_fail} vi loi (se thu lai o lan chay sau)")
        print(f"[refresh] {client.n_requests} request HTTP")
    return {"wallets": len(wallets), "new": n_new, "dropped": dropped, "failed": n_fail}
