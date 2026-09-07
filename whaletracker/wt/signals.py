"""Buoc 5b - do dong thai vi va tinh cac chi so thi truong.

Hai cau hoi khac nhau, tra loi bang hai nhom so lieu khac nhau:

  1. "Cac vi dang lam gi?"  -> stance: dem dong tien rong cua tung vi trong tung giai doan
  2. "Gia con tang duoc khong?" -> signals: dong tien vao/ra san, ap luc chot lai, dong luong gia

KHONG dung AI, khong co mo hinh hop den. Moi chi so deu la mot phep dem tren du lieu
on-chain hoac gia, va deu duoc in ra kem cach doc de ban tu kiem chung duoc.

Canh bao ve ban chat: day la DO AP LUC dang co, khong phai loi tien tri. Dong tien on-chain
cho biet nguoi ta DA lam gi, khong cho biet ho SAP lam gi.
"""
from __future__ import annotations

from . import config

BUY_SIDES = ("BUY", "WITHDRAW")
SELL_SIDES = ("SELL", "DEPOSIT")


# ---------------- dong thai tung vi ----------------

def wallet_stance(conn, wallet: str, since_ts: int, price_now: float) -> dict:
    """Vi nay da lam gi ke tu `since_ts`? Mua them / xa / giu nguyen / thoat het."""
    r = conn.execute("""
        SELECT
          COALESCE(SUM(CASE WHEN side IN ('BUY','WITHDRAW') AND ts>=? THEN amount END),0) AS bought,
          COALESCE(SUM(CASE WHEN side IN ('SELL','DEPOSIT') AND ts>=? THEN amount END),0) AS sold,
          COALESCE(SUM(CASE WHEN side IN ('BUY','WITHDRAW') AND ts>=? THEN usd END),0)   AS bought_usd,
          COALESCE(SUM(CASE WHEN side IN ('SELL','DEPOSIT') AND ts>=? THEN usd END),0)   AS sold_usd,
          COUNT(CASE WHEN ts>=? AND side<>'INTERNAL' THEN 1 END) AS n,
          MAX(CASE WHEN ts>=? AND side<>'INTERNAL' THEN ts END)  AS last_act
        FROM trades WHERE wallet=?""",
        (since_ts,) * 6 + (wallet,)).fetchone()

    st = conn.execute(
        "SELECT balance_uni, avg_cost, copy_score, klass, name FROM wallet_stats WHERE wallet=?",
        (wallet,)).fetchone()
    bal = (st["balance_uni"] if st else 0.0) or 0.0
    bought, sold = r["bought"], r["sold"]
    net = bought - sold
    turnover = bought + sold

    # Nguong 2%: dong tien nho hon 2% so du hien tai la nhieu, khong phai quyet dinh.
    ref = max(bal, turnover, 1.0)
    if r["n"] == 0:
        stance, why = "HOLD", "khong giao dich gi trong giai doan nay"
    elif bal <= max(0.01 * ref, 1.0) and sold > 0:
        stance, why = "THOAT HET", f"ban {sold:,.0f} UNI, so du con gan nhu bang 0"
    elif net > 0.02 * ref:
        stance, why = "MUA THEM", f"mua rong {net:,.0f} UNI"
    elif net < -0.02 * ref:
        stance, why = "XA", f"ban rong {-net:,.0f} UNI"
    else:
        stance, why = "HOLD", f"co giao dich 2 chieu nhung dong rong khong dang ke ({net:+,.0f} UNI)"

    return {
        "wallet": wallet, "stance": stance, "why": why,
        "bought": bought, "sold": sold, "net": net,
        "bought_usd": r["bought_usd"], "sold_usd": r["sold_usd"],
        "n_trades": r["n"], "last_act": r["last_act"],
        "balance": bal, "avg_cost": (st["avg_cost"] if st else None) or 0.0,
        "copy_score": (st["copy_score"] if st else 0.0) or 0.0,
        "klass": st["klass"] if st else None,
        "name": st["name"] if st else None,
        "unreal_usd": bal * (price_now - ((st["avg_cost"] if st else 0) or 0)) if bal > 0 else 0.0,
    }


def stances(conn, cfg, since_ts: int, price_now: float, min_score: float = 0.0) -> list[dict]:
    rows = conn.execute("""
        SELECT wallet FROM wallet_stats
        WHERE klass IN ('whale_candidate','fund','retail') AND copy_score >= ?
        ORDER BY copy_score DESC""", (min_score,)).fetchall()
    return [wallet_stance(conn, r["wallet"], since_ts, price_now) for r in rows]


# ---------------- chi so thi truong ----------------

def cex_netflow(conn, since_ts: int, bucket_days: int = 1) -> list[dict]:
    """Dong UNI vao/ra cac dia chi san, gom theo ngay.

    Vao san  = ap luc ban (nguoi ta chuyen len de ban)
    Ra san   = tich luy   (nguoi ta rut ve vi lanh de giu)
    Day la chi so on-chain duoc dung rong rai nhat, va cung la chi so de doc sai nhat:
    no do Y DINH, khong do hanh dong da xay ra.
    """
    step = bucket_days * 86400
    return [dict(r) for r in conn.execute("""
        SELECT (t.ts / ?) * ? AS bucket,
               SUM(CASE WHEN a2.klass='cex' THEN t.amount ELSE 0 END) AS vao_san,
               SUM(CASE WHEN a1.klass='cex' THEN t.amount ELSE 0 END) AS ra_san
        FROM transfers t
        LEFT JOIN addresses a1 ON a1.addr = t.from_addr
        LEFT JOIN addresses a2 ON a2.addr = t.to_addr
        WHERE t.ts >= ? AND (a1.klass='cex' OR a2.klass='cex')
        GROUP BY bucket ORDER BY bucket""", (step, step, since_ts))]


def whale_flow(conn, since_ts: int, bucket_days: int = 1) -> list[dict]:
    """Mua rong / ban rong cua rieng nhom vi whale, gom theo ngay."""
    step = bucket_days * 86400
    return [dict(r) for r in conn.execute("""
        SELECT (t.ts / ?) * ? AS bucket,
               SUM(CASE WHEN t.side IN ('BUY','WITHDRAW')  THEN t.amount ELSE 0 END) AS mua,
               SUM(CASE WHEN t.side IN ('SELL','DEPOSIT')  THEN t.amount ELSE 0 END) AS ban
        FROM trades t JOIN addresses a ON a.addr = t.wallet
        WHERE t.ts >= ? AND a.klass = 'whale_candidate' AND t.side <> 'INTERNAL'
        GROUP BY bucket ORDER BY bucket""", (step, step, since_ts))]


def supply_in_profit(conn, price_now: float) -> dict:
    """Bao nhieu phan tram luong UNI dang nam giu co gia von THAP HON gia hien tai.

    Cang cao thi cang nhieu nguoi dang lai -> ap luc chot lai cang lon.
    Chi tinh tren phan ton kho ma ta BIET gia von, nen la mau, khong phai toan thi truong.
    """
    r = conn.execute("""
        SELECT
          COALESCE(SUM(CASE WHEN avg_cost > 0 AND avg_cost < ? THEN balance_uni END),0) AS lai,
          COALESCE(SUM(CASE WHEN avg_cost > 0 AND avg_cost >= ? THEN balance_uni END),0) AS lo,
          COUNT(CASE WHEN avg_cost > 0 AND balance_uni > 0 THEN 1 END) AS n_vi
        FROM wallet_stats
        WHERE balance_uni > 0 AND basis_conf >= 0.5
          AND klass IN ('whale_candidate','fund','retail')""",
        (price_now, price_now)).fetchone()
    tong = r["lai"] + r["lo"]
    return {"lai": r["lai"], "lo": r["lo"], "n_vi": r["n_vi"],
            "ty_le_lai": (r["lai"] / tong) if tong > 0 else None}
