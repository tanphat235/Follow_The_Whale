"""Cac phan bo sung cua bao cao: dong thai vi, chi so du bao, va goi y cho danh muc.

Tach rieng khoi report.py de khong phai mo xe chuoi HTML lon trong do.
"""
from __future__ import annotations

import html

from . import config, market, resistance, signals

_STANCE_CLASS = {"MUA THEM": "st-buy", "XA": "st-sell",
                 "HOLD": "st-hold", "THOAT HET": "st-out"}
_DIR_CLASS = {market.BULL: "d-up", market.BEAR: "d-dn", market.NEUTRAL: "d-nt"}
_DIR_ARROW = {market.BULL: "▲", market.BEAR: "▼", market.NEUTRAL: "—"}


def _usd(v, dash="-"):
    if v is None:
        return dash
    return f"{'-' if v < 0 else ''}${abs(v):,.0f}"


# ---------------- 1. Tom tat dau trang ----------------

def hero(conn, price_now, entry_price, milestones) -> str:
    ms = {m["key"]: m for m in milestones}
    top = ms.get("cycle_top", {})
    low = ms.get("correction_low", {})
    peak = top.get("price") or price_now
    gain = (price_now / entry_price - 1) if entry_price else 0.0
    from_low = (price_now / low["price"] - 1) if low.get("price") else None
    cards = [
        (f"${price_now:,.3f}", "Gia UNI hien tai"),
        (f"{price_now / peak - 1:+.1%}", f"So voi dinh ${peak:.3f}"),
        (f"+{gain:.0%}", f"Danh muc cua ban (vao ${entry_price:.2f})"),
        (f"+{from_low:.0%}" if from_low else "-", f"Tu day ${low.get('price', 0):.3f} ({low.get('date', '')})"),
    ]
    return ('<div class="hero">' + "".join(
        f'<div class="card kpi"><div class="v">{html.escape(v)}</div>'
        f'<div class="k">{html.escape(k)}</div></div>' for v, k in cards) + "</div>")


# ---------------- 2. Dong thai vi ----------------

def stance_section(conn, cfg, since_ts, price_now, contract, addr_link, limit=25) -> str:
    rows = signals.stances(conn, cfg, since_ts, price_now,
                           min_score=float(cfg["monitor"]["min_score_to_watch"]))
    rows = [r for r in rows if r["n_trades"] > 0 or r["balance"] > 0]
    rows.sort(key=lambda r: -(abs(r["net"]) * (r["avg_cost"] or 1)))

    tally = {}
    for r in rows:
        tally[r["stance"]] = tally.get(r["stance"], 0) + 1

    if not rows:
        return ("<p class='muted'>Chua co du lieu on-chain trong giai doan nay. "
                "Chay <code>python -m wt refresh</code> de cap nhat.</p>")

    chips = " ".join(
        f'<span class="st {_STANCE_CLASS.get(k, "st-hold")}">{html.escape(k)}: {v}</span>'
        for k, v in sorted(tally.items(), key=lambda x: -x[1]))

    body = []
    for r in rows[:limit]:
        cls = _STANCE_CLASS.get(r["stance"], "st-hold")
        body.append(f"""
<tr><td>{addr_link(r['wallet'], contract, r['name'] or '')}</td>
  <td><span class="st {cls}">{html.escape(r['stance'])}</span>
      <div class="why">{html.escape(r['why'])}</div></td>
  <td>{r['copy_score']:.2f}</td>
  <td class="pos">{r['bought']:,.0f}</td>
  <td class="neg">{r['sold']:,.0f}</td>
  <td class="{'pos' if r['net'] >= 0 else 'neg'}">{r['net']:+,.0f}</td>
  <td>{r['balance']:,.0f}</td>
  <td>{('$%.3f' % r['avg_cost']) if r['avg_cost'] else '-'}</td>
  <td class="{'pos' if r['unreal_usd'] >= 0 else 'neg'}">{_usd(r['unreal_usd'])}</td>
  <td class="muted">{config.fmt_ts(r['last_act'], False)}</td></tr>""")

    return f"""
<p class="sub" style="margin:-4px 0 10px">{chips}</p>
<div class="card scroll"><table style="min-width:1050px">
<thead><tr><th>Vi</th><th>Dong thai</th><th>Diem</th><th>Da mua</th><th>Da ban</th>
<th>Rong</th><th>Con giu</th><th>Gia von</th><th>Lai chua chot</th><th>Lan cuoi</th></tr></thead>
<tbody>{''.join(body)}</tbody></table></div>"""


# ---------------- 3. Chi so du bao ----------------

def coverage_notice(conn) -> str:
    """Canh bao ro khi du lieu on-chain khong con phu toan thi truong."""
    cov = market.coverage(conn)
    if not cov["partial"]:
        return ""
    return (f'<div class="card warn"><b>Pham vi du lieu on-chain.</b> Quet toan thi truong '
            f'chi den <b>{config.fmt_ts(cov["full_until"], False)}</b>. Tu do den '
            f'{config.fmt_ts(cov["latest"], False)}, du lieu chi bao gom '
            f'<b>{cov["cohort_wallets"]} vi dang theo doi</b>, khong phai ca thi truong '
            f'(chenh lech toi 20 lan ve so transfer moi tuan). Cac chi so on-chain trong '
            f'giai doan nay doc theo NHOM VI NAY, dung suy ra toan thi truong. '
            f'Chay <code>python -m wt backfill</code> khi Blockscout het chan de lap day.</div>')


def signals_section(conn, price_now, since_ts) -> str:
    sig = market.price_signals(conn) + market.onchain_signals(conn, price_now, since_ts)
    if not sig:
        return "<p class='muted'>Chua du du lieu de tinh chi so.</p>"
    a = market.assess(sig)
    pos = (a["diem"] + 1) / 2 * 100

    rows = "".join(f"""
<tr><td style="text-align:left">{html.escape(s['ten'])}
      <div class="why">{html.escape(s['giai_thich'])}</div></td>
  <td>{html.escape(s['doc'])}</td>
  <td class="dir {_DIR_CLASS[s['huong']]}">{_DIR_ARROW[s['huong']]} {html.escape(s['huong'])}</td>
  <td class="muted">{s['trong_so']:.1f}</td></tr>""" for s in sig)

    return coverage_notice(conn) + f"""
<div class="card">
  <div style="font-size:19px;font-weight:680">{html.escape(a['ket_luan'])}
    <span class="muted" style="font-size:14px;font-weight:400">&nbsp;diem {a['diem']:+.2f}</span></div>
  <div class="gauge"><i style="left:calc({pos:.1f}% - 2px)"></i></div>
  <div class="legend"><span>Giam</span><span style="margin-left:auto">Tang</span></div>
  <p class="sub" style="margin:8px 0 0">{html.escape(a['dien_giai'])}
     &nbsp;·&nbsp; {a['n_tang']} chi so tang · {a['n_trung']} trung tinh · {a['n_giam']} giam</p>
</div>
<div class="card scroll" style="margin-top:12px"><table style="min-width:760px">
<thead><tr><th style="text-align:left">Chi so</th><th>Gia tri</th><th>Huong</th><th>Trong so</th></tr></thead>
<tbody>{rows}</tbody></table></div>"""


# ---------------- 4. Goi y cho danh muc ----------------

def levels(conn, price_now):
    """Cac muc gia dang chu y, tinh tu du lieu that chu khong ve tay."""
    cl = [c for _, c in market.closes(conn)]
    cand = market.daily_candles(conn)
    out = {}
    if len(cl) >= 50:
        out["ma20"] = sum(cl[-20:]) / 20
        out["ma50"] = sum(cl[-50:]) / 50
    if cand:
        out["dinh"] = max(c["hi"] for c in cand)
        # day gan nhat cua 10 ngay truoc khi tang manh
        recent = cand[-21:-7] if len(cand) >= 21 else cand
        out["ho_tro_gan"] = min(c["lo"] for c in recent) if recent else None
    out["gia"] = price_now
    return out


def plan_section(conn, price_now, entry_price, lv) -> str:
    gain = price_now / entry_price - 1
    ma20 = lv.get("ma20")
    ma50 = lv.get("ma50")
    dinh = lv.get("dinh")
    ht = lv.get("ho_tro_gan")

    chips = []
    for label, val in (("Gia von cua ban", entry_price), ("Gia hien tai", price_now),
                       ("MA20", ma20), ("MA50", ma50),
                       ("Dinh chu ky", dinh), ("Ho tro gan", ht)):
        if val:
            chips.append(f"<div><b>{html.escape(label)}</b><br>${val:,.3f}</div>")

    return f"""
<div class="card">
  <div class="lvl">{''.join(chips)}</div>
  <p class="sub" style="margin:6px 0 0">Ban dang lai <b>{gain:+.0%}</b> tren toan bo danh muc.
  Toan bo von nam trong mot tai san duy nhat.</p>
</div>"""


# ---------------- 5. Khang cu / ho tro ----------------

def resistance_section(conn, price_now, entry_price) -> str:
    """Ba phuong phap doc lap: volume profile, dinh dao chieu, thong ke lich su."""
    vp = resistance.volume_profile(conn, price_now)
    sw = resistance.swing_levels(conn, price_now)
    st = resistance.stretch_study(conn)
    cur = resistance.current_stretch(conn, price_now)

    if not vp:
        return ("<p class='muted'>Chua co du lieu nen ngay dai han. "
                "Chay <code>python -m wt report</code> lai sau khi backfill.</p>")

    vp_rows = "".join(f"""
<tr><td style="text-align:left"><b>${v['lo']:.1f} &ndash; ${v['hi']:.1f}</b></td>
  <td>{v['volume'] / 1e6:,.0f}M UNI</td>
  <td>{v['gap']:+.1%}</td>
  <td><span class="btrack" style="display:inline-block;width:150px;vertical-align:middle">
      <i style="width:{v['strength'] * 100:.0f}%"></i></span></td></tr>""" for v in vp)

    hi_chips = "".join(
        f"<div><b>${h['price']:.2f}</b><br>{h['gap']:+.0%}</div>" for h in sw["highs"][:7])

    # Day cua song HIEN TAI (2026) khac voi ho tro lich su tu cac nam truoc.
    recent_lows = [l for l in sw["lows"] if l["ts"] >= config.parse_when("2026-06-01")]
    lo_chips = "".join(
        f"<div><b>${l['price']:.2f}</b><br>{l['gap']:+.0%} &middot; {config.fmt_ts(l['ts'], False)[5:]}</div>"
        for l in recent_lows[:4]) or "<div class='muted'>khong co day nao trong song hien tai</div>"

    st_html = ""
    if st:
        ev_rows = "".join(f"""
<tr><td style="text-align:left">{config.fmt_ts(e['ts'], False)}</td>
  <td>{e['gap']:+.0%}</td>
  <td class="{'pos' if e['fwd7'] >= 0 else 'neg'}">{e['fwd7']:+.0%}</td>
  <td class="{'pos' if e['fwd30'] >= 0 else 'neg'}">{e['fwd30']:+.0%}</td>
  <td class="pos">{e['best']:+.0%}</td>
  <td class="neg">{e['worst']:+.0%}</td></tr>"""
            for e in sorted(st["events"], key=lambda x: x["ts"]))
        st_html = f"""
<h3 style="font-size:15px;margin:26px 0 8px">Lich su: khi UNI vuot MA20 tren +40% thi sau do ra sao</h3>
<p class="sub" style="margin:0 0 10px">Hien tai gia vuot MA20 <b>{cur.get('gap20', 0):+.1%}</b>.
Da xay ra <b>{st['n']} lan</b> tuong tu trong 6 nam.</p>
<div class="hero">
  <div class="card kpi"><div class="v">{st['med_fwd7']:+.1%}</div><div class="k">trung vi sau 7 ngay</div></div>
  <div class="card kpi"><div class="v">{st['med_fwd30']:+.1%}</div><div class="k">trung vi sau 30 ngay</div></div>
  <div class="card kpi"><div class="v">{st['n_up']}/{st['n']}</div><div class="k">so lan con tang sau 30 ngay</div></div>
  <div class="card kpi"><div class="v" style="color:var(--neg)">{st['med_worst']:+.1%}</div>
      <div class="k">trung vi muc sut sau nhat trong 30 ngay</div></div>
</div>
<div class="card scroll" style="margin-top:12px"><table style="min-width:620px">
<thead><tr><th style="text-align:left">Ngay</th><th>Vuot MA20</th><th>Sau 7 ngay</th>
<th>Sau 30 ngay</th><th>Cao nhat</th><th>Thap nhat</th></tr></thead>
<tbody>{ev_rows}</tbody></table></div>
<div class="card warn" style="margin-top:12px">Phan bo nay <b>luong cuc</b>: hoac bung no tiep,
hoac la dinh cuc bo - gan nhu khong co truong hop o giua. Nghia la khong the du doan huong.
Nhung co mot dieu gan nhu chac chan: <b>moi lan deu co nhip sut trong 30 ngay sau do</b>.
Do la ly do de CHIA NHO ma chot thay vi doan mot lan.</div>"""

    return f"""
<div class="card">
  <p class="sub" style="margin:0 0 10px"><b>Vung gia co nhieu hang da sang tay nhat, nam tren gia hien tai.</b>
  Nguoi mua o do dang ket von - ho ban ra khi gia ve hoa von, tao nguon cung treo lo lung.</p>
  <div class="scroll"><table style="min-width:560px">
  <thead><tr><th style="text-align:left">Vung gia</th><th>Khoi luong</th>
  <th>Cach gia nay</th><th>Do can</th></tr></thead>
  <tbody>{vp_rows}</tbody></table></div>
</div>

<h3 style="font-size:15px;margin:26px 0 8px">Dinh dao chieu phia tren (noi gia da tung quay dau)</h3>
<div class="card"><div class="lvl">{hi_chips}</div></div>

<h3 style="font-size:15px;margin:26px 0 8px">Day cua song hien tai (moc pha vo cau truc tang)</h3>
<div class="card"><div class="lvl">{lo_chips}</div>
<p class="sub" style="margin:8px 0 0">Cac muc ho tro $6-8 trong lich su la tu nam 2024-2025,
khong phai cau truc cua song nay - dung nham lan hai loai.</p></div>
{st_html}"""
