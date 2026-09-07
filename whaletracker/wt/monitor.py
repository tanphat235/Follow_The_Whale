"""Buoc 7 - theo doi realtime va gui canh bao.

Rat nhe: moi vong chi 1 lan goi getLogs cho toan bo UNI ke tu block da quet lan truoc,
roi loc cuc bo theo watchlist. ~1 call / 45 giay = ~1,900 call/ngay.

Che do --replay quan trong nhat: phat lai 1 ngay qua khu tu du lieu da co trong DB
de xem tool LE RA da ban canh bao gi. Do la bai kiem tra gia tri that cua ca he thong,
lam duoc ma khong ton them mot request nao.
"""
from __future__ import annotations

import html as html_mod
import json
import time
from datetime import datetime, timezone

from . import alerts, config, db
from .http import Client
from .sources import prices as price_src
from .sources.blockscout import Blockscout

BUY_SIDES = {"BUY", "WITHDRAW"}
SELL_SIDES = {"SELL", "DEPOSIT"}

# Ba muc do khan cap. Muc thap nhat BAM THEO nguong watchlist trong config:
# neu khong, ha min_score_to_watch xuong duoi 3.0 se tao ra watchlist ma khong
# vi nao kich hoat duoc canh bao - im lang mot cach nguy hiem.
TIER_BANDS = [
    (4.0, {"SELL": ("🔴", "WHALE DIEM CAO DANG BAN"),
           "BUY":  ("🟢", "WHALE DIEM CAO DANG MUA")}),
    (3.0, {"SELL": ("🟠", "Whale dang ban"),
           "BUY":  ("🟡", "Whale dang mua")}),
]
TIER_FLOOR = {"SELL": ("⚪", "Vi theo doi dang ban"),
              "BUY":  ("⚪", "Vi theo doi dang mua")}


def load_watchlist(cfg, conn) -> dict[str, dict]:
    min_score = float(cfg["monitor"]["min_score_to_watch"])
    rows = conn.execute("""
        SELECT wallet, copy_score, name, klass, balance_uni, avg_cost, total_pnl, roi, edge
        FROM wallet_stats WHERE copy_score >= ?
          AND klass IN ('whale_candidate','fund','retail')
        ORDER BY copy_score DESC""", (min_score,)).fetchall()
    return {r["wallet"]: dict(r) for r in rows}


def _tier(score: float, direction: str, min_score: float) -> tuple[str, str] | None:
    for threshold, mapping in TIER_BANDS:
        if score >= threshold:
            return mapping.get(direction)
    # Da nam trong watchlist thi luon dang duoc bao, chi la muc do thap hon.
    return TIER_FLOOR.get(direction) if score >= min_score else None


def classify_move(conn, counterparty: str, wallet_receives: bool) -> tuple[str, float, str]:
    row = conn.execute("SELECT klass FROM addresses WHERE addr=?", (counterparty,)).fetchone()
    cp_klass = row["klass"] if row else None
    from .ledger import _side_for
    side, conf = _side_for(cp_klass, wallet_receives)
    return side, conf, (cp_klass or "khong ro")


def format_alert(event: dict) -> tuple[str, str, str]:
    w = event["watch"]
    icon, label = event["icon"], event["label"]
    price = event["price"]
    usd = event["usd"]
    direction = "MUA" if event["side"] in BUY_SIDES else "BAN"
    proof = {"BUY": "qua DEX", "SELL": "qua DEX",
             "WITHDRAW": "rut tu CEX", "DEPOSIT": "nap len CEX"}.get(event["side"], "chuyen khoan")

    # Moi truong nay deu co the thieu (chua co gia, vi chua du lich su).
    # Canh bao thieu thong tin van hon canh bao khong gui duoc vi crash dinh dang.
    usd_txt = f"${usd:,.0f}" if usd is not None else "khong ro gia tri"
    pnl_txt = f"${w['total_pnl']:,.0f}" if w.get("total_pnl") is not None else "chua ro"

    subject = f"{icon} UNI {direction} {event['amount']:,.0f} ({usd_txt}) - vi {w['copy_score']:.1f}d"

    lines = [
        f"{icon} {label}",
        "",
        f"Vi        : {event['wallet']}",
        f"Diem tin cay: {w['copy_score']:.2f}/5" + (f"  ({w['name']})" if w.get("name") else ""),
        f"Hanh dong : {direction} {event['amount']:,.2f} UNI  ({proof})",
        f"Gia UNI   : ${price:.4f}" if price else "Gia UNI   : khong lay duoc",
        f"Gia tri   : {usd_txt}",
        f"Do tin cay suy luan: {event['conf']:.0%}",
        f"Doi tac   : {event['counterparty']} [{event['cp_klass']}]",
        "",
        f"Lich su vi : PnL {pnl_txt}"
        + (f" | ROI {w['roi']:.0%}" if w.get("roi") is not None else "")
        + (f" | edge {w['edge']:+.2f}" if w.get("edge") is not None else ""),
        f"Gia von TB : ${w['avg_cost']:.4f}" if w.get("avg_cost") else "",
        f"Thoi diem  : {config.fmt_ts(event['ts'])} UTC",
        "",
        f"Tx: https://etherscan.io/tx/{event['tx_hash']}",
        f"Vi: https://etherscan.io/address/{event['wallet']}",
    ]
    text = "\n".join(l for l in lines if l != "")

    body = html_mod.escape(text).replace("\n", "<br>")
    html_body = (f"<div style='font:14px/1.6 -apple-system,Segoe UI,sans-serif'>"
                 f"<h2 style='margin:0 0 12px'>{html_mod.escape(icon + ' ' + label)}</h2>"
                 f"<pre style='font:13px/1.55 ui-monospace,Menlo,monospace;"
                 f"background:#f6f7f9;padding:14px;border-radius:8px;white-space:pre-wrap'>"
                 f"{body}</pre></div>")
    return subject, text, html_body


def process_transfers(cfg, conn, rows, watch, client, spot_price=None, send=True) -> list[dict]:
    """rows: dict co tx_hash, log_index, block_number, ts, from, to, amount."""
    min_usd = float(cfg["monitor"]["alert_usd_min"])
    min_score = float(cfg["monitor"]["min_score_to_watch"])
    events = []

    for t in rows:
        for wallet, counterparty, receives in ((t["to"], t["from"], True),
                                               (t["from"], t["to"], False)):
            w = watch.get(wallet)
            if not w:
                continue
            if conn.execute(
                    "SELECT 1 FROM alerts_sent WHERE tx_hash=? AND log_index=? AND wallet=?",
                    (t["tx_hash"], t["log_index"], wallet)).fetchone():
                continue

            side, conf, cp_klass = classify_move(conn, counterparty, receives)
            if side == "INTERNAL":
                continue
            direction = "BUY" if side in BUY_SIDES else ("SELL" if side in SELL_SIDES else None)
            if direction is None:
                continue

            price = spot_price
            if price is None and client is not None:
                price = price_src.spot(client, cfg["token"]["price_pair"])
            usd = (t["amount"] * price) if price else None
            # Khong co gia thi khong the loc theo nguong USD - van bao, va ghi ro la khong ro gia,
            # con hon im lang. (usd == 0 la gia tri hop le nen phai so sanh voi None, khong dung falsy.)
            if usd is not None and usd < min_usd:
                continue

            tier = _tier(w["copy_score"], direction, min_score)
            if not tier:
                continue
            icon, label = tier

            events.append({
                "wallet": wallet, "watch": w, "side": side, "conf": conf,
                "counterparty": counterparty, "cp_klass": cp_klass,
                "amount": t["amount"], "price": price, "usd": usd,
                "ts": t["ts"], "tx_hash": t["tx_hash"], "icon": icon, "label": label,
            })

            if send:
                subject, text, html_body = format_alert(events[-1])
                res = alerts.send_all(cfg, subject, text, html_body)
                print(f"  [alert] {icon} {wallet[:12]}... {side} "
                      f"{t['amount']:,.0f} UNI -> {res}", flush=True)
                conn.execute(
                    "INSERT OR REPLACE INTO alerts_sent(tx_hash,log_index,wallet,sent_ts)"
                    " VALUES(?,?,?,?)",
                    (t["tx_hash"], t["log_index"], wallet, int(time.time())))
                conn.commit()
    return events


# ---------------- che do chay ----------------

def do_test_alert(cfg) -> int:
    text = ("Day la canh bao THU cua UNI Whale Tracker.\n\n"
            "Neu ban nhan duoc tin nhan nay, kenh canh bao da thong suot.\n"
            f"Thoi diem: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    res = alerts.send_all(cfg, "🔔 UNI Whale Tracker - canh bao thu", text,
                          f"<pre>{html_mod.escape(text)}</pre>")
    print(f"[test-alert] {json.dumps(res, ensure_ascii=False)}")
    return 0 if any(v == "ok" for v in res.values()) else 1


def do_replay(cfg, conn, day: str) -> int:
    """Phat lai 1 ngay tu du lieu DA CO trong DB - khong ton request nao."""
    start = config.parse_when(day)
    end = start + 86400
    watch = load_watchlist(cfg, conn)
    if not watch:
        print("[replay] watchlist rong. Chay 'python -m wt score' truoc.")
        return 1

    from .sources.prices import PriceBook
    book = PriceBook(conn)
    rows = [dict(tx_hash=r["tx_hash"], log_index=r["log_index"], block_number=r["block_number"],
                 ts=r["ts"], **{"from": r["from_addr"], "to": r["to_addr"]}, amount=r["amount"])
            for r in conn.execute(
                "SELECT * FROM transfers WHERE ts >= ? AND ts < ? ORDER BY ts",
                (start, end)).fetchall()]

    print(f"[replay] {config.fmt_ts(start, False)}: {len(rows):,} transfer, "
          f"{len(watch)} vi trong watchlist")

    events = []
    for r in rows:
        events += process_transfers(cfg, conn, [r], watch, None,
                                    spot_price=book.at(r["ts"]), send=False)

    if not events:
        print("[replay] khong co canh bao nao trong ngay nay.")
        return 0

    print(f"[replay] tool LE RA da ban {len(events)} canh bao:\n")
    for e in sorted(events, key=lambda x: -(x["usd"] or 0)):
        direction = "MUA" if e["side"] in BUY_SIDES else "BAN"
        usd = f"${e['usd']:>12,.0f}" if e["usd"] is not None else f"{'? USD':>13}"
        price = f"@ ${e['price']:.4f}" if e["price"] is not None else "@ (khong ro gia)"
        print(f"  {e['icon']} {config.fmt_ts(e['ts'])}  {direction} "
              f"{e['amount']:>12,.0f} UNI  {usd} {price}  "
              f"{e['wallet'][:12]}... ({e['watch']['copy_score']:.1f}d)")
    return 0


def do_watch(cfg, conn, once=False) -> int:
    client = Client(cfg)
    bs = Blockscout(client, cfg["sources"]["blockscout_base"], verbose=False)
    poll = int(cfg["monitor"]["poll_sec"])
    watch = load_watchlist(cfg, conn)
    if not watch:
        print("[monitor] watchlist rong. Chay 'python -m wt score' truoc.")
        return 1

    last = db.meta_get(conn, "monitor_last_block")
    cursor = int(last) if last else bs.latest_block()
    print(f"[monitor] theo doi {len(watch)} vi | tu block {cursor:,} | "
          f"quet moi {poll}s | Ctrl+C de dung")
    print(f"[monitor] kenh canh bao: "
          f"{[k for k, v in cfg['alerts'].items() if v.get('enabled')] or 'CHUA BAT KENH NAO'}")

    last_heartbeat = None
    while True:
        try:
            tip = bs.latest_block()
            if tip > cursor:
                rows = []
                for batch in bs.sweep(cfg.contract, cursor + 1, tip, chunk=max(tip - cursor, 1)):
                    for t in batch:
                        try:
                            amt = int(t["raw_value"]) / (10 ** cfg.decimals)
                        except (TypeError, ValueError):
                            continue
                        if amt >= 0.01:      # bo log bui, giong buoc backfill
                            rows.append({**t, "amount": amt})
                process_transfers(cfg, conn, rows, watch, client, send=True)
                cursor = tip
                db.meta_set(conn, "monitor_last_block", cursor)
                conn.commit()

            # nhip tim moi ngay de biet tool con song
            hour = datetime.now(timezone.utc)
            if (hour.hour == int(cfg["monitor"]["heartbeat_hour_utc"])
                    and last_heartbeat != hour.date()):
                alerts.send_all(cfg, "💚 UNI Whale Tracker con song",
                                f"Dang theo doi {len(watch)} vi. Block hien tai {cursor:,}.")
                last_heartbeat = hour.date()

        except KeyboardInterrupt:
            print("\n[monitor] da dung.")
            return 0
        except Exception as exc:
            print(f"[monitor] loi vong quet: {type(exc).__name__}: {str(exc)[:160]}", flush=True)

        if once:
            return 0
        time.sleep(poll)


def run(cfg, conn, test_alert=False, replay=None, once=False) -> int:
    if test_alert:
        return do_test_alert(cfg)
    if replay:
        return do_replay(cfg, conn, replay)
    return do_watch(cfg, conn, once=once)
