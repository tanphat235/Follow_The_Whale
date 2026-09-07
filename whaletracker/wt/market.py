"""Chi so thi truong - tra loi "gia con tang duoc khong" bang so lieu, khong bang cam tinh.

Moi chi so deu la mot phep dem tren du lieu that va deu kem cach doc, de ban tu
kiem chung duoc thay vi phai tin mot con so hop den.

GIOI HAN PHAI NOI TRUOC: day la do AP LUC HIEN CO, khong phai du bao. Chi so on-chain
cho biet nguoi ta DA lam gi, khong cho biet ho SAP lam gi. Khong chi so nao o day
tung du bao dung mot cu dao chieu nao - chung chi mo ta trang thai hien tai.
"""
from __future__ import annotations

import math

BULL, NEUTRAL, BEAR = "TANG", "TRUNG TINH", "GIAM"


def daily_candles(conn) -> list[dict]:
    """Gop nen 1 phut thanh nen ngay."""
    return [dict(r) for r in conn.execute(
        "SELECT (ts/86400)*86400 AS d, MAX(high) hi, MIN(low) lo,"
        " SUM(volume) vol, COUNT(*) n FROM prices GROUP BY d ORDER BY d")]


def closes(conn) -> list[tuple[int, float]]:
    """(ngay, gia dong cua) - gia dong cua = nen 1m cuoi cung trong ngay."""
    return [(r["d"], r["c"]) for r in conn.execute(
        "SELECT (p.ts/86400)*86400 AS d, p.close AS c FROM prices p"
        " JOIN (SELECT (ts/86400)*86400 AS d2, MAX(ts) mx FROM prices GROUP BY d2) g"
        "   ON p.ts = g.mx AND (p.ts/86400)*86400 = g.d2 ORDER BY d")]


def _rsi(vals: list[float], period: int = 14) -> float | None:
    if len(vals) < period + 1:
        return None
    gains = losses = 0.0
    for a, b in zip(vals[-period - 1:-1], vals[-period:]):
        diff = b - a
        gains += max(diff, 0.0)
        losses += max(-diff, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - (100 / (1 + rs))


def _sma(vals: list[float], n: int) -> float | None:
    return sum(vals[-n:]) / n if len(vals) >= n else None


def _ret(vals: list[float], n: int) -> float | None:
    if len(vals) <= n or vals[-n - 1] <= 0:
        return None
    return vals[-1] / vals[-n - 1] - 1


def _sig(ten, gia_tri, doc, huong, trong_so, giai_thich):
    return {"ten": ten, "gia_tri": gia_tri, "doc": doc,
            "huong": huong, "trong_so": trong_so, "giai_thich": giai_thich}


def price_signals(conn) -> list[dict]:
    days = closes(conn)
    if len(days) < 30:
        return []
    cl = [c for _, c in days]
    px = cl[-1]
    cand = daily_candles(conn)
    vols = [c["vol"] for c in cand]
    highs = [c["hi"] for c in cand]
    out: list[dict] = []

    r7, r30 = _ret(cl, 7), _ret(cl, 30)
    out.append(_sig(
        "Dong luong 7 ngay", r7, f"{r7:+.1%}" if r7 is not None else "-",
        BULL if (r7 or 0) > 0.10 else (BEAR if (r7 or 0) < -0.05 else NEUTRAL), 1.0,
        "Gia 7 ngay qua. Tang manh = xu huong con hieu luc, nhung tang cang nhanh "
        "thi nhip dieu chinh sau do cang sau."))
    out.append(_sig(
        "Dong luong 30 ngay", r30, f"{r30:+.1%}" if r30 is not None else "-",
        BULL if (r30 or 0) > 0.20 else (BEAR if (r30 or 0) < 0 else NEUTRAL), 1.0,
        "Xu huong trung han - khung quyet dinh xu huong chinh con song hay khong."))

    peak = max(highs)
    dd = px / peak - 1
    out.append(_sig(
        "Cach dinh chu ky", dd, f"{dd:+.1%} (dinh ${peak:.3f})",
        BULL if dd > -0.05 else (NEUTRAL if dd > -0.15 else BEAR), 0.8,
        "Sat dinh = con manh nhung het bien an toan. Roi xa dinh qua 15% = xu huong da gay."))

    ma20, ma50 = _sma(cl, 20), _sma(cl, 50)
    if ma20:
        gap = px / ma20 - 1
        out.append(_sig(
            "Vuot MA20", gap, f"{gap:+.1%}",
            BEAR if gap > 0.35 else (BULL if gap > 0.05 else NEUTRAL), 1.2,
            "Tren MA20 la xu huong tang. Nhung vuot QUA XA (tren 35%) la qua nong - "
            "gia thuong phai quay ve test lai duong nay truoc khi di tiep."))
    if ma50:
        gap50 = px / ma50 - 1
        out.append(_sig(
            "Vuot MA50", gap50, f"{gap50:+.1%}",
            BULL if gap50 > 0 else BEAR, 0.8,
            "Tren MA50 nghia la xu huong trung han van la tang."))

    if len(vols) >= 28:
        recent, prior = sum(vols[-7:]) / 7, sum(vols[-28:-7]) / 21
        ratio = (recent / prior) if prior > 0 else None
        out.append(_sig(
            "Khoi luong 7d vs 21d truoc", ratio, f"x{ratio:.2f}" if ratio else "-",
            BULL if (ratio or 0) > 1.2 else (BEAR if (ratio or 1) < 0.7 else NEUTRAL), 1.0,
            "Gia tang KEM khoi luong tang = tien that dang vao. Gia tang ma khoi luong "
            "can dan = het luc mua, day la canh bao dao chieu kinh dien."))

    rsi = _rsi(cl)
    if rsi is not None:
        out.append(_sig(
            "RSI 14 ngay", rsi, f"{rsi:.0f}",
            BEAR if rsi > 75 else (BULL if rsi > 55 else (BEAR if rsi < 40 else NEUTRAL)), 1.0,
            "Tren 75 = qua mua, rui ro dieu chinh cao. 55-75 = xu huong tang khoe. "
            "Duoi 40 = yeu."))

    rets = [cl[i] / cl[i - 1] - 1 for i in range(max(1, len(cl) - 14), len(cl))]
    if len(rets) > 3:
        vol14 = math.sqrt(sum(r * r for r in rets) / len(rets)) * math.sqrt(365)
        out.append(_sig(
            "Bien dong nam hoa 14d", vol14, f"{vol14:.0%}",
            BEAR if vol14 > 1.5 else NEUTRAL, 0.6,
            "Bien dong cao khong quyet dinh huong, nhung quyet dinh ban nen vao size bao nhieu."))

    if len(cl) >= 21:
        w1, w2, w3 = cl[-7:], cl[-14:-7], cl[-21:-14]
        hh = max(w1) > max(w2) > max(w3)
        hl = min(w1) > min(w2) > min(w3)
        out.append(_sig(
            "Cau truc dinh/day", 1.0 if (hh and hl) else (0.5 if hh else 0.0),
            "dinh sau cao hon + day sau cao hon" if (hh and hl)
            else ("dinh sau cao hon" if hh else "cau truc yeu"),
            BULL if (hh and hl) else (NEUTRAL if hh else BEAR), 1.0,
            "Dinh sau cao hon VA day sau cao hon la dinh nghia sach nhat cua xu huong tang."))

    return out


def coverage(conn) -> dict:
    """Xac dinh moc ma du lieu on-chain ngung phu TOAN THI TRUONG.

    Sau moc nay chi con du lieu cua nhom vi da refresh (~120 dia chi), khong phai
    ca thi truong. Trinh bay so lieu sau moc do nhu chi bao toan thi truong la SAI:
    da do duoc chenh lech toi 20 lan ve so transfer moi tuan (78k -> 3k).
    """
    row = conn.execute(
        "SELECT MAX(ts) mx FROM transfers WHERE src IN ('logs','deep')").fetchone()
    full_until = row["mx"] if row else None
    latest = conn.execute("SELECT MAX(ts) mx FROM transfers").fetchone()["mx"]
    n_cohort = conn.execute(
        "SELECT COUNT(DISTINCT wallet) n FROM trades WHERE ts > ?",
        (full_until or 0,)).fetchone()["n"]
    return {"full_until": full_until, "latest": latest, "cohort_wallets": n_cohort,
            "partial": bool(full_until and latest and latest > full_until + 86400)}


def onchain_signals(conn, price_now: float, since_ts: int) -> list[dict]:
    """Chi so tu du lieu on-chain cua nhom vi dang theo doi."""
    out: list[dict] = []

    r = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN t.side IN ('BUY','WITHDRAW') THEN t.amount END),0) mua,"
        " COALESCE(SUM(CASE WHEN t.side IN ('SELL','DEPOSIT') THEN t.amount END),0) ban,"
        " COALESCE(SUM(CASE WHEN t.side='DEPOSIT' THEN t.amount END),0) nap_san,"
        " COUNT(DISTINCT t.wallet) n_vi"
        " FROM trades t JOIN addresses a ON a.addr=t.wallet"
        " WHERE t.ts >= ? AND t.side<>'INTERNAL'"
        "   AND a.klass IN ('whale_candidate','fund')", (since_ts,)).fetchone()

    tong = r["mua"] + r["ban"]
    if tong > 0:
        net_ratio = (r["mua"] - r["ban"]) / tong
        out.append(_sig(
            "Whale mua rong hay ban rong", net_ratio,
            f"mua {r['mua']:,.0f} - ban {r['ban']:,.0f} UNI ({net_ratio:+.0%})",
            BULL if net_ratio > 0.1 else (BEAR if net_ratio < -0.1 else NEUTRAL), 1.4,
            "Nhom vi lon dang gom hay dang xa. CHI tinh tren nhom vi dang theo doi, "
            "khong phai toan thi truong. Van la hanh vi thuc cua tien lon, khong phai suy dien tu gia."))

        dep_ratio = (r["nap_san"] / r["ban"]) if r["ban"] > 0 else 0.0
        out.append(_sig(
            "Ty le hang ban di thang len san", dep_ratio, f"{dep_ratio:.0%}",
            BEAR if dep_ratio > 0.6 else NEUTRAL, 0.8,
            "Nap len san thuong la de ban. Ty le cao = ap luc ban dang tich tu. "
            "Nhung day la suy doan, khong phai bang chung da ban."))

    p = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN avg_cost>0 AND avg_cost < ? THEN balance_uni END),0) lai,"
        " COALESCE(SUM(CASE WHEN avg_cost>0 AND avg_cost >= ? THEN balance_uni END),0) lo"
        " FROM wallet_stats WHERE balance_uni>0 AND basis_conf>=0.5"
        "   AND klass IN ('whale_candidate','fund','retail')",
        (price_now, price_now)).fetchone()
    tot = p["lai"] + p["lo"]
    if tot > 0:
        pct = p["lai"] / tot
        out.append(_sig(
            "Luong hang dang lai", pct, f"{pct:.0%} ({p['lai']:,.0f} UNI dang lai)",
            BEAR if pct > 0.95 else (BULL if pct < 0.7 else NEUTRAL), 1.0,
            "Gan 100% dang lai nghia la ai cung co ly do chot - ap luc ban tiem an lon. "
            "Chi tinh tren mau vi biet gia von, khong phai toan thi truong."))

    return out


def assess(signals: list[dict]) -> dict:
    """Gop cac chi so lai. KHONG phai xac suat thong ke - chi la tong hop co trong so."""
    score = w_tot = 0.0
    for s in signals:
        val = {BULL: 1.0, NEUTRAL: 0.0, BEAR: -1.0}[s["huong"]]
        score += val * s["trong_so"]
        w_tot += s["trong_so"]
    norm = (score / w_tot) if w_tot else 0.0
    if norm >= 0.35:
        verdict = "NGHIENG VE TANG TIEP"
        why = "Phan lon chi so ung ho xu huong tang con hieu luc."
    elif norm <= -0.35:
        verdict = "NGHIENG VE DIEU CHINH"
        why = "Phan lon chi so canh bao ap luc giam."
    else:
        verdict = "HAI CHIEU - KHONG CO THE MANH RO RANG"
        why = "Cac chi so mau thuan nhau. Day la trang thai hay gap nhat va cung la luc de sai nhat."
    return {"diem": norm, "ket_luan": verdict, "dien_giai": why,
            "n_tang": sum(1 for s in signals if s["huong"] == BULL),
            "n_giam": sum(1 for s in signals if s["huong"] == BEAR),
            "n_trung": sum(1 for s in signals if s["huong"] == NEUTRAL)}
