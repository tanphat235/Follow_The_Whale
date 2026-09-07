"""Buoc 5 - cham diem tin cay copy-trade tren thang 5.

Tong 6 hang muc = 5.00 diem, tru them phan phat toi da 2.00.
Moi hang muc deu duoc luu chi tiet vao score_json de bao cao GIAI THICH DUOC
tai sao vi nay 4.2 diem con vi kia 1.8 - khong phai con so hop den.

Triet ly cham diem: cai ta muon khong phai "vi nao lai nhieu tien nhat" ma la
"vi nao ma neu minh bam theo thi minh lai tien". Hai cai do khac nhau:
mot con bot MEV co the lai rat nhieu ma ban khong the copy noi.
"""
from __future__ import annotations

import json
import math
import time

# Trong so toi da cua tung hang muc
W_PROFIT, W_TIMING, W_REPEAT, W_COPY, W_PURITY, W_LIVE = 1.25, 1.00, 1.00, 0.75, 0.60, 0.40
MAX_PENALTY = 2.00

CLASS_FACTOR = {
    "whale_candidate": 1.00,
    "fund": 0.90,
    "retail": 0.75,
    "market_maker": 0.15,
    "cex_shuttle": 0.0,
    "contract_other": 0.20,
    "router": 0.05,
    "dex_pool": 0.0, "cex": 0.0, "mev_bot": 0.0, "bridge": 0.0, "burn": 0.0,
    "protocol": 0.10, "token_contract": 0.0,
}

# Volume UNI trung binh 1 ngay tren thi truong, dung de uoc luong tac dong gia.
DAILY_VOLUME_UNI = 3_000_000


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _ramp(x, lo, hi):
    """lo -> 0.0, hi -> 1.0, tuyen tinh o giua. Chiu duoc hi < lo (dao chieu)."""
    if x is None:
        return 0.0
    if hi == lo:
        return 1.0 if x >= hi else 0.0
    return _clamp((x - lo) / (hi - lo))


def _extra_stats(conn) -> dict[str, dict]:
    """Cac chi so bo sung lay truc tiep tu bang trades."""
    out: dict[str, dict] = {}
    rows = conn.execute("""
        SELECT wallet,
               MAX(amount) AS max_amt,
               SUM(CASE WHEN side='DEPOSIT' THEN amount ELSE 0 END) AS dep_amt,
               SUM(CASE WHEN side IN ('SELL','DEPOSIT') THEN amount ELSE 0 END) AS disp_amt,
               SUM(CASE WHEN side IN ('BUY','SELL') THEN 1 ELSE 0 END) AS n_dex,
               COUNT(DISTINCT cp_klass) AS n_venue,
               COUNT(DISTINCT counterparty) AS n_cp
        FROM trades WHERE side <> 'INTERNAL' GROUP BY wallet
    """).fetchall()
    for r in rows:
        out[r["wallet"]] = dict(r)

    # do gian cach giua cac lenh -> vi gom tu tu thi ban con kip bam theo
    for r in conn.execute("""
        SELECT wallet, ts FROM trades WHERE side <> 'INTERNAL' AND ts IS NOT NULL
        ORDER BY wallet, ts
    """).fetchall():
        slot = out.setdefault(r["wallet"], {})
        slot.setdefault("_ts", []).append(r["ts"])
    for slot in out.values():
        ts = slot.pop("_ts", [])
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        slot["median_gap"] = sorted(gaps)[len(gaps) // 2] if gaps else None
    return out


def score_wallet(row, extra: dict, now_ts: int, is_scam: bool) -> dict:
    parts: dict[str, dict] = {}

    # --- 1. Loi nhuan (1.25) - phai dat CA HAI: ty suat tot VA so tien du lon ---
    roi, pnl = row["roi"], row["total_pnl"] or 0.0
    if pnl <= 0 or roi is None or roi <= 0:
        p_profit = 0.0
        why = f"khong co lai (PnL ${pnl:,.0f})"
    else:
        roi_part = _ramp(roi, 0.0, 1.00)                       # ROI 100% = tron diem
        usd_part = _ramp(math.log10(max(pnl, 1)), 4.0, 5.4)    # $10k -> $250k
        p_profit = W_PROFIT * min(roi_part, usd_part)          # min = doi hoi ca hai
        why = f"ROI {roi:.0%}, PnL ${pnl:,.0f}"
    parts["loi_nhuan"] = {"diem": round(p_profit, 3), "toi_da": W_PROFIT, "vi_sao": why}

    # --- 2. Timing edge (1.00) - do "mua re ban dat" ---
    edge = row["edge"]
    p_timing = W_TIMING * _ramp(edge, 0.0, 0.5) if edge is not None else 0.0
    bp, sp = row["buy_pctile"], row["sell_pctile"]
    parts["timing"] = {
        "diem": round(p_timing, 3), "toi_da": W_TIMING,
        "vi_sao": (f"mua o phan vi gia {bp:.0%}, ban o phan vi {sp:.0%} -> edge {edge:+.2f}"
                   if edge is not None else "khong du du lieu gia 2 chieu"),
    }

    # --- 3. Lap lai duoc (1.00) - 1 lan an may khong phai ky nang ---
    rt = row["round_trips"] or 0
    base = {0: 0.0, 1: 0.20, 2: 0.45, 3: 0.70, 4: 0.85}.get(rt, 1.0)
    wr = row["win_rate"] if row["win_rate"] is not None else 0.5
    p_repeat = W_REPEAT * base * (0.4 + 0.6 * wr)
    parts["lap_lai"] = {
        "diem": round(p_repeat, 3), "toi_da": W_REPEAT,
        "vi_sao": f"{rt} vong mua-ban tron ven, ty le thang {wr:.0%}",
    }

    # --- 4. Copy duoc (0.75) - tin hieu co kip bam theo khong ---
    gap = extra.get("median_gap")
    gap_part = _ramp(math.log10(max(gap, 1)), 1.7, 3.6) if gap else 0.3  # 50s -> 1.1 gio
    max_amt = extra.get("max_amt") or 0
    impact = _clamp(max_amt / DAILY_VOLUME_UNI)       # lenh cang to cang day gia truoc khi ban vao
    klass = row["klass"] or ""
    mev_factor = 0.0 if klass == "mev_bot" else 1.0
    p_copy = W_COPY * gap_part * (1 - impact) * mev_factor
    parts["copy_duoc"] = {
        "diem": round(p_copy, 3), "toi_da": W_COPY,
        "vi_sao": (f"gian cach lenh trung vi {gap / 3600:.1f}h" if gap else "chi co 1 lenh")
                  + f", lenh lon nhat {max_amt:,.0f} UNI (~{impact:.0%} volume ngay)"
                  + (", la bot MEV nen khong copy duoc" if klass == "mev_bot" else ""),
    }

    # --- 5. Tin hieu sach (0.60) - phai la vi nguoi that, co lap truong ro rang ---
    cf = CLASS_FACTOR.get(klass, 0.2)
    gross = row["gross_uni"] or 0
    conviction = abs(row["net_uni"] or 0) / gross if gross > 0 else 0
    p_purity = W_PURITY * cf * (0.4 + 0.6 * conviction)
    parts["tin_hieu_sach"] = {
        "diem": round(p_purity, 3), "toi_da": W_PURITY,
        "vi_sao": f"loai '{klass}' (he so {cf:.2f}), do dut khoat {conviction:.0%}",
    }

    # --- 6. Con song (0.40) - vi da bo di thi copy ai ---
    last = row["last_ts"]
    days = ((now_ts - last) / 86400) if last else 999
    p_live = W_LIVE * _ramp(days, 90, 14)   # dao chieu: cang moi cang cao
    parts["con_song"] = {
        "diem": round(p_live, 3), "toi_da": W_LIVE,
        "vi_sao": f"giao dich gan nhat cach day {days:.0f} ngay",
    }

    subtotal = p_profit + p_timing + p_repeat + p_copy + p_purity + p_live

    # --- Phat ---
    penalties: list[dict] = []

    def penalise(amount: float, why: str):
        if amount > 0.001:
            penalties.append({"tru": round(amount, 3), "vi_sao": why})

    bc = row["basis_conf"] if row["basis_conf"] is not None else 0.0
    penalise((0.6 - bc) * 1.0 if bc < 0.6 else 0.0,
             f"gia von khong chac chan (do tin cay {bc:.0%}) - thieu lich su hoac thieu gia")

    first = row["first_ts"]
    if first and (now_ts - first) < 30 * 86400:
        penalise(0.4, f"vi moi, chi {(now_ts - first) / 86400:.0f} ngay tuoi")

    if (row["n_trades"] or 0) <= 1:
        penalise(0.3, "chi co dung 1 giao dich - khong the ket luan gi")

    disp = extra.get("disp_amt") or 0
    dep = extra.get("dep_amt") or 0
    if disp > 0 and dep / disp > 0.9:
        penalise(0.3, f"{dep / disp:.0%} hang ban ra chi di vao CEX - khong xac minh duoc "
                      f"la da ban that hay chi chuyen kho")

    if rt == 0:
        penalise(0.4, "chua khep kin duoc vong mua-ban nao")

    if pnl < 0:
        penalise(0.5, f"dang lo ${pnl:,.0f}")

    if is_scam:
        penalise(1.0, "bi Blockscout danh dau la dia chi lua dao")

    total_penalty = min(MAX_PENALTY, sum(p["tru"] for p in penalties))
    final = _clamp(subtotal - total_penalty, 0.0, 5.0)

    return {
        "copy_score": round(final, 2),
        "detail": {
            "tong_truoc_phat": round(subtotal, 3),
            "tong_phat": round(total_penalty, 3),
            "hang_muc": parts,
            "phat": penalties,
        },
    }


def score_mm(row, extra: dict) -> float:
    """Diem 'giong Market Maker' /5. Cang cao cang la vi lai gia, KHONG phai vi de copy."""
    gross = row["gross_uni"] or 0
    net_ratio = abs(row["net_uni"] or 0) / gross if gross > 0 else 1.0
    s = 0.0
    s += 1.5 * (1 - _ramp(net_ratio, 0.0, 0.15))          # ton kho gan nhu khong doi
    s += 1.0 * _ramp(math.log10(max(row["n_trades"] or 1, 1)), 1.3, 3.0)  # 20 -> 1000 lenh
    s += 1.0 * _ramp(extra.get("n_cp") or 0, 5, 80)       # nhieu doi tac
    s += 0.75 * _ramp(extra.get("n_venue") or 0, 1, 4)    # cham nhieu loai venue
    if (row["klass"] or "") == "market_maker":
        s += 0.75
    return round(_clamp(s, 0, 5), 2)


def run(cfg, conn, verbose=True) -> dict:
    now_ts = int(time.time())
    extra = _extra_stats(conn)
    scam = {r["addr"] for r in conn.execute(
        "SELECT addr FROM addresses WHERE is_scam=1").fetchall()}

    rows = conn.execute("SELECT * FROM wallet_stats").fetchall()
    if not rows:
        raise SystemExit("[score] chua co du lieu. Chay 'python -m wt ledger' truoc.")

    updates = []
    for r in rows:
        ex = extra.get(r["wallet"], {})
        res = score_wallet(r, ex, now_ts, r["wallet"] in scam)
        updates.append((res["copy_score"], score_mm(r, ex),
                        json.dumps(res["detail"], ensure_ascii=False), r["wallet"]))

    conn.executemany(
        "UPDATE wallet_stats SET copy_score=?, mm_score=?, score_json=? WHERE wallet=?", updates)
    conn.commit()

    if verbose:
        print(f"[score] cham diem {len(updates):,} vi")
        bands = conn.execute("""
            SELECT CASE WHEN copy_score>=4 THEN '4.0-5.0 copy tu tin'
                        WHEN copy_score>=3 THEN '3.0-3.9 copy size nho'
                        WHEN copy_score>=2 THEN '2.0-2.9 chi theo doi'
                        ELSE '0.0-1.9 bo qua' END band, COUNT(*) n
            FROM wallet_stats GROUP BY band ORDER BY band DESC""").fetchall()
        for b in bands:
            print(f"    {b['band']:24s} {b['n']:>6,}")

        # Kiem tra tinh tao: ha tang khong duoc lot top
        bad = conn.execute("""
            SELECT wallet, klass, copy_score FROM wallet_stats
            WHERE copy_score >= 3 AND klass IN ('cex','cex_shuttle','dex_pool','router','mev_bot','market_maker')
            ORDER BY copy_score DESC LIMIT 5""").fetchall()
        if bad:
            print("    [CANH BAO] vi ha tang lot vao vung diem cao - cong thuc can xem lai:")
            for b in bad:
                print(f"      {b['wallet'][:12]}... {b['klass']} {b['copy_score']}")
        else:
            print("    [OK ] khong co vi CEX/shuttle/pool/router/MM/MEV nao dat >=3.0 diem")

    return {"scored": len(updates)}
