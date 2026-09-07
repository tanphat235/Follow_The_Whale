"""Buoc 4 - dich transfer thanh hanh dong mua/ban that, roi tinh cost basis va PnL.

Nguyen tac trung thuc quan trong nhat trong file nay:
  Chuyen token len CEX KHONG PHAI la da ban. Do chi la suy doan (conf 0.70).
  Chuyen sang 1 EOA la KHONG BIET la gi - co the la vi phu cua chinh chu.
  Vi vay OTC_IN/OTC_OUT chi lam thay doi so du, TUYET DOI khong sinh ra PnL ao.
"""
from __future__ import annotations

from . import config
from .sources.prices import PriceBook

# counterparty_class -> (side khi vi NHAN, conf), (side khi vi GUI, conf)
SIDE_MAP = {
    "dex_pool":   (("BUY", 0.95), ("SELL", 0.95)),
    "router":     (("BUY", 0.85), ("SELL", 0.85)),
    "mev_bot":    (("BUY", 0.55), ("SELL", 0.55)),
    "market_maker": (("BUY", 0.60), ("SELL", 0.60)),
    "cex":        (("WITHDRAW", 0.60), ("DEPOSIT", 0.70)),
    "bridge":     (("OTC_IN", 0.20), ("OTC_OUT", 0.20)),
    "burn":       (("OTC_IN", 0.20), ("OTC_OUT", 0.20)),
}
DEFAULT_SIDE = (("OTC_IN", 0.30), ("OTC_OUT", 0.30))

ACQUIRE = {"BUY", "WITHDRAW"}
DISPOSE = {"SELL", "DEPOSIT"}
# Chi cac vi nay moi duoc dung so; con lai la ha tang, khong co "vi the" de noi den.
LEDGER_CLASSES = ("whale_candidate", "market_maker", "mev_bot", "fund", "retail",
                  "cex_shuttle")


def _side_for(cp_klass: str | None, wallet_receives: bool) -> tuple[str, float]:
    pair = SIDE_MAP.get(cp_klass or "", DEFAULT_SIDE)
    return pair[0] if wallet_receives else pair[1]


def build_trades(cfg, conn, book: PriceBook, verbose=True) -> int:
    klass = {r["addr"]: r["klass"] for r in
             conn.execute("SELECT addr, klass FROM addresses").fetchall()}
    cluster = {r["addr"]: r["cluster_id"] for r in
               conn.execute("SELECT addr, cluster_id FROM addresses WHERE cluster_id IS NOT NULL").fetchall()}

    # Dat san volume: 'retail' chiem da so dia chi (cua so 138 ngay co the cho 50-100k vi).
    # Dung so cho vi chi giao dich vai chuc USD la lang cong suc va lam nhieu bao cao.
    # Vi 'whale_candidate' da vuot nguong 25,000 UNI o buoc classify nen luon duoc giu.
    floor = float(cfg["thresholds"].get("ledger_min_volume_uni", 2500))
    big = {r["addr"] for r in conn.execute("""
        SELECT addr FROM (
            SELECT to_addr AS addr, amount FROM transfers
            UNION ALL SELECT from_addr, amount FROM transfers
        ) GROUP BY addr HAVING SUM(amount) >= ?""", (floor,))}
    tracked = {a for a, k in klass.items()
               if k in LEDGER_CLASSES and (k != "retail" or a in big)}

    conn.execute("DELETE FROM trades")
    INSERT = ("INSERT OR REPLACE INTO trades(tx_hash,log_index,wallet,ts,block_number,side,"
              "amount,price,usd,conf,counterparty,cp_klass) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)")

    # Duyet theo luong va ghi theo lo: du lieu that co ~600k transfer, gom het vao
    # list Python roi executemany mot lan se ngon vai tram MB.
    cursor = conn.execute(
        "SELECT tx_hash, log_index, block_number, ts, from_addr, to_addr, amount"
        " FROM transfers WHERE amount > 0 ORDER BY ts, block_number, log_index")
    writer = conn.cursor()

    batch, n_total, n_internal = [], 0, 0
    for r in cursor:
        src, dst, amt, ts = r["from_addr"], r["to_addr"], r["amount"], r["ts"]
        price = book.at(ts)
        for wallet, counterparty, receives in ((dst, src, True), (src, dst, False)):
            if wallet not in tracked:
                continue
            # Dieu chuyen noi bo giua cac vi cung chu -> khong phai mua ban
            if (cluster.get(wallet) and cluster.get(wallet) == cluster.get(counterparty)) \
                    or wallet == counterparty:
                side, conf = "INTERNAL", 1.0
                n_internal += 1
            else:
                side, conf = _side_for(klass.get(counterparty), receives)
            batch.append((r["tx_hash"], r["log_index"], wallet, ts, r["block_number"],
                          side, amt, price, (amt * price) if price else None, conf,
                          counterparty, klass.get(counterparty)))
        if len(batch) >= 50_000:
            writer.executemany(INSERT, batch)
            n_total += len(batch)
            batch.clear()

    if batch:
        writer.executemany(INSERT, batch)
        n_total += len(batch)
    conn.commit()

    if verbose:
        print(f"[ledger] {n_total:,} ban ghi giao dich cho {len(tracked):,} vi "
              f"({n_internal:,} la dieu chuyen noi bo, khong tinh mua ban)")
    return n_total


def _wmean(pairs: list[tuple[float, float]]) -> float | None:
    """Trung binh co trong so. pairs = [(gia_tri, trong_so)]"""
    tw = sum(w for _, w in pairs if w > 0)
    if tw <= 0:
        return None
    return sum(v * w for v, w in pairs if w > 0) / tw


def wallet_ledger(cfg, conn, wallet: str, book: PriceBook, price_now: float | None) -> dict:
    """Chay lai toan bo lich su 1 vi theo thu tu thoi gian -> cost basis, PnL, chi so timing."""
    rows = conn.execute(
        "SELECT ts, side, amount, price, usd, conf, cp_klass FROM trades"
        " WHERE wallet=? ORDER BY ts, tx_hash, log_index", (wallet,)).fetchall()

    # Tach ton kho lam hai phan. Day la diem mau chot ve tinh trung thuc:
    # token vao vi ma khong biet gia (chuyen tu EOA la, hoac ngoai pham vi du lieu gia)
    # TUYET DOI khong duoc coi la "gia von = 0" - lam vay se de ra ROI ao hang nghin %.
    costed_bal = 0.0      # phan ton kho biet gia von
    uncosted_bal = 0.0    # phan ton kho khong biet gia von
    bal = 0.0             # tong = costed + uncosted
    avg = 0.0             # gia von binh quan, chi tinh tren costed_bal
    realized = 0.0
    cost_deployed = 0.0
    costed_in = 0.0       # tong luong da vao vi co kem gia
    uncosted_in = 0.0     # tong luong da vao vi khong kem gia
    unknown_inventory = 0.0   # phan ban ra vuot ca so du -> hang co truoc cua so

    n_buy = n_sell = 0
    uni_bought = uni_sold = 0.0
    buy_pcts: list[tuple[float, float]] = []
    sell_pcts: list[tuple[float, float]] = []
    fwd: dict[str, list[tuple[float, float]]] = {"24h": [], "72h": [], "7d": []}
    horizons = {"24h": 86400, "72h": 3 * 86400, "7d": 7 * 86400}

    # theo doi tung "chan" vi the de dem vong mua-ban tron ven
    legs: list[dict] = []
    leg: dict | None = None

    biggest_buy = biggest_sell = None
    spacing: list[float] = []
    last_ts = None
    n_priced = n_total = 0

    for r in rows:
        side, amt, price, ts = r["side"], r["amount"], r["price"], r["ts"]
        if side == "INTERNAL":
            continue
        n_total += 1
        if price:
            n_priced += 1
        if last_ts is not None and ts:
            spacing.append(ts - last_ts)
        if ts:
            last_ts = ts

        if side in ACQUIRE:
            if price:
                # Gia von binh quan gia quyen - CHI tren phan ton kho da biet gia.
                costed_bal += amt
                avg = ((avg * (costed_bal - amt)) + (amt * price)) / costed_bal
                cost_deployed += amt * price
                costed_in += amt
                buy_pcts.append((book.pctile(price) or 0.0, amt * price))
                for key, sec in horizons.items():
                    fr = book.forward_return(ts, sec)
                    if fr is not None:
                        fwd[key].append((fr, amt * price))   # mua: gia len sau do = tot
                if biggest_buy is None or amt > biggest_buy["amount"]:
                    biggest_buy = {"amount": amt, "ts": ts, "price": price}
            else:
                # Mua nhung khong tra duoc gia -> KHONG duoc coi la gia von 0.
                uncosted_bal += amt
                uncosted_in += amt
            bal = costed_bal + uncosted_bal
            uni_bought += amt
            n_buy += 1
            if leg is None:
                leg = {"peak": bal, "pnl": 0.0, "start": ts}
            leg["peak"] = max(leg["peak"], bal)

        elif side in DISPOSE:
            held = costed_bal + uncosted_bal
            if amt > held:
                unknown_inventory += amt - held   # hang co truoc cua so, khong ro mua luc nao
            take = min(amt, held)
            # Khong the biet dong coin nao roi vi truoc, nen tru theo ty le.
            from_costed = take * (costed_bal / held) if held > 0 else 0.0
            from_uncosted = take - from_costed

            if price:
                if from_costed > 0:
                    gain = from_costed * (price - avg)
                    realized += gain
                    if leg:
                        leg["pnl"] += gain
                sell_pcts.append((book.pctile(price) or 0.0, amt * price))
                for key, sec in horizons.items():
                    fr = book.forward_return(ts, sec)
                    if fr is not None:
                        fwd[key].append((-fr, amt * price))  # ban: gia giam sau do = tot
                if biggest_sell is None or amt > biggest_sell["amount"]:
                    biggest_sell = {"amount": amt, "ts": ts, "price": price}

            costed_bal = max(0.0, costed_bal - from_costed)
            uncosted_bal = max(0.0, uncosted_bal - from_uncosted)
            bal = costed_bal + uncosted_bal
            uni_sold += amt
            n_sell += 1
            # Dong chan khi da thoat >=80% dinh vi the
            if leg and bal <= 0.2 * leg["peak"]:
                legs.append(leg)
                leg = None

        else:  # OTC_IN / OTC_OUT - chuyen toi/tu EOA la, co the la vi phu cua chinh chu.
            # Chi doi so du, KHONG sinh PnL va KHONG duoc tinh la gia von 0.
            if side == "OTC_IN":
                uncosted_bal += amt
                uncosted_in += amt
            else:
                held = costed_bal + uncosted_bal
                take = min(amt, held)
                from_costed = take * (costed_bal / held) if held > 0 else 0.0
                costed_bal = max(0.0, costed_bal - from_costed)
                uncosted_bal = max(0.0, uncosted_bal - (take - from_costed))
            bal = costed_bal + uncosted_bal

    # `leg` con dang mo o day thi CO Y KHONG gop vao `legs`: vi the chua dong khong
    # phai la mot vong mua-ban tron ven. Gop vao se lam vi chi mua ma chua ban bao gio
    # cung duoc tinh 1 vong -> thoat hinh phat "chua khep kin vong nao" va an diem lap-lai.

    # Lai chua thuc hien chi tinh tren phan ton kho BIET gia von.
    unreal = (costed_bal * (price_now - avg)) if (price_now and avg > 0) else 0.0
    total_pnl = realized + unreal

    # ROI chi co y nghia khi ta biet gia von cua phan lon hang da di qua vi.
    # Vi co 3,000 UNI mua co gia nhung ban ra 112,000 UNI thi mau so qua nho,
    # chia ra se cho ROI hang nghin % vo nghia -> tra None de score bo qua muc nay.
    moved = costed_in + uncosted_in + unknown_inventory
    basis_coverage = (costed_in / moved) if moved > 0 else 0.0
    roi = (total_pnl / cost_deployed) if (cost_deployed > 0 and basis_coverage >= 0.5) else None

    buy_p = _wmean(buy_pcts)
    sell_p = _wmean(sell_pcts)
    edge = (sell_p - buy_p) if (buy_p is not None and sell_p is not None) else None

    # Do tin cay gia von = ty le giao dich co gia x ty le hang biet nguon goc.
    price_cov = (n_priced / n_total) if n_total else 0.0
    basis_conf = max(0.0, min(1.0, price_cov * basis_coverage))

    milestones = {m["key"]: m for m in cfg.milestone_items()}
    top = milestones.get("cycle_top")
    low = milestones.get("cycle_low")
    top_prox_hours = top_prox_ratio = bottom_prox_ratio = None
    if biggest_sell and top:
        top_prox_hours = abs(biggest_sell["ts"] - top["ts"]) / 3600 if biggest_sell["ts"] else None
        if top.get("price"):
            top_prox_ratio = biggest_sell["price"] / top["price"]
    if biggest_buy and low and low.get("price"):
        bottom_prox_ratio = biggest_buy["price"] / low["price"]

    wins = sum(1 for l in legs if l["pnl"] > 0)
    return {
        "wallet": wallet,
        "n_trades": n_total, "n_buys": n_buy, "n_sells": n_sell,
        "uni_bought": uni_bought, "uni_sold": uni_sold,
        "net_uni": uni_bought - uni_sold, "gross_uni": uni_bought + uni_sold,
        "balance_uni": bal, "avg_cost": avg,
        "realized_pnl": realized, "unrealized_pnl": unreal, "total_pnl": total_pnl,
        "roi": roi, "cost_deployed": cost_deployed,
        "buy_pctile": buy_p, "sell_pctile": sell_p, "edge": edge,
        "round_trips": len(legs), "win_rate": (wins / len(legs)) if legs else None,
        "fwd_24h": _wmean(fwd["24h"]), "fwd_72h": _wmean(fwd["72h"]), "fwd_7d": _wmean(fwd["7d"]),
        "top_prox_hours": top_prox_hours, "top_prox_ratio": top_prox_ratio,
        "bottom_prox_ratio": bottom_prox_ratio,
        "median_spacing_sec": (sorted(spacing)[len(spacing) // 2] if spacing else None),
        "basis_conf": basis_conf,
        "first_ts": rows[0]["ts"] if rows else None,
        "last_ts": rows[-1]["ts"] if rows else None,
    }


def run(cfg, conn, verbose=True) -> dict:
    book = PriceBook(conn)
    if not len(book):
        raise SystemExit("[ledger] chua co du lieu gia. Chay 'python -m wt backfill' truoc.")
    if verbose:
        lo, hi = book.span
        print(f"[ledger] {len(book):,} nen gia tu {config.fmt_ts(lo)} den {config.fmt_ts(hi)}")

    build_trades(cfg, conn, book, verbose)
    price_now = book.close[-1] if book.close else None

    wallets = [r["wallet"] for r in conn.execute(
        "SELECT DISTINCT wallet FROM trades").fetchall()]
    results = []
    for i, wallet in enumerate(wallets, 1):
        results.append(wallet_ledger(cfg, conn, wallet, book, price_now))
        if verbose and i % 200 == 0:
            print(f"  [ledger] {i:,}/{len(wallets):,} vi", flush=True)

    conn.execute("DELETE FROM wallet_stats")
    meta = {r["addr"]: (r["klass"], r["name"], r["cluster_id"]) for r in conn.execute(
        "SELECT addr, klass, name, cluster_id FROM addresses")}
    conn.executemany(
        "INSERT INTO wallet_stats(wallet,klass,name,cluster_id,n_trades,n_buys,n_sells,"
        "uni_bought,uni_sold,net_uni,gross_uni,balance_uni,avg_cost,realized_pnl,"
        "unrealized_pnl,total_pnl,roi,buy_pctile,sell_pctile,edge,round_trips,win_rate,"
        "fwd_24h,fwd_72h,fwd_7d,top_prox_hours,top_prox_ratio,bottom_prox_ratio,"
        "first_ts,last_ts,basis_conf) VALUES(" + ",".join("?" * 31) + ")",
        [(d["wallet"], *meta.get(d["wallet"], (None, None, None)),
          d["n_trades"], d["n_buys"], d["n_sells"], d["uni_bought"], d["uni_sold"],
          d["net_uni"], d["gross_uni"], d["balance_uni"], d["avg_cost"], d["realized_pnl"],
          d["unrealized_pnl"], d["total_pnl"], d["roi"], d["buy_pctile"], d["sell_pctile"],
          d["edge"], d["round_trips"], d["win_rate"], d["fwd_24h"], d["fwd_72h"], d["fwd_7d"],
          d["top_prox_hours"], d["top_prox_ratio"], d["bottom_prox_ratio"],
          d["first_ts"], d["last_ts"], d["basis_conf"]) for d in results])
    conn.commit()

    if verbose:
        print(f"[ledger] dung so xong cho {len(results):,} vi (gia hien tai ${price_now:.4f})")
    return {"wallets": len(results), "price_now": price_now}
