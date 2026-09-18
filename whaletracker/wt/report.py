"""Buoc 6 - xuat bao cao HTML tu chua + CSV + watchlist.json.

HTML khong dung CDN, khong dung thu vien ngoai: bieu do la SVG tu ve.
Mo bang trinh duyet bat ky, ke ca khi khong co mang.
"""
from __future__ import annotations

import csv
import html
import json
from datetime import datetime, timezone

from . import config, report_sections as rs

BAND = [
    (4.0, "copy tu tin", "s-a"),
    (3.0, "copy size nho", "s-b"),
    (2.0, "chi theo doi", "s-c"),
    (0.0, "bo qua", "s-d"),
]


def band_of(score):
    for lo, label, cls in BAND:
        if (score or 0) >= lo:
            return label, cls
    return "bo qua", "s-d"


def _fmt(v, kind="num", dash="-"):
    if v is None:
        return dash
    if kind == "usd":
        sign = "-" if v < 0 else ""
        return f"{sign}${abs(v):,.0f}"
    if kind == "pct":
        return f"{v:.0%}"
    if kind == "sig":
        return f"{v:+.2f}"
    if kind == "uni":
        return f"{v:,.0f}"
    if kind == "price":
        return f"${v:.4f}"
    return f"{v:,.2f}"


# ---------------- bieu do SVG ----------------

def _price_chart(conn, cfg, wallets, width=1180, height=380, pad=52):
    rows = conn.execute("SELECT ts, close FROM prices ORDER BY ts").fetchall()
    if len(rows) < 2:
        return "<p class='muted'>Chua co du lieu gia de ve bieu do.</p>"

    step = max(1, len(rows) // 900)
    pts = [(r["ts"], r["close"]) for r in rows[::step]]
    t0, t1 = pts[0][0], pts[-1][0]
    lo = min(p for _, p in pts)
    hi = max(p for _, p in pts)
    span = (hi - lo) or 1.0
    lo -= span * 0.08
    hi += span * 0.08

    def X(ts):
        return pad + (ts - t0) / max(t1 - t0, 1) * (width - pad - 20)

    def Y(p):
        return height - pad - (p - lo) / (hi - lo) * (height - pad - 24)

    line = " ".join(f"{X(t):.1f},{Y(p):.1f}" for t, p in pts)
    area = f"{pad},{height - pad} {line} {X(t1):.1f},{height - pad}"

    svg = [f'<svg viewBox="0 0 {width} {height}" class="chart" '
           f'preserveAspectRatio="xMidYMid meet" role="img" '
           f'aria-label="Gia UNI va cac lenh mua ban cua whale">']
    svg.append('<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
               '<stop offset="0%" stop-color="var(--accent)" stop-opacity=".22"/>'
               '<stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>'
               '</linearGradient></defs>')

    # luoi ngang + nhan gia
    for i in range(5):
        p = lo + (hi - lo) * i / 4
        y = Y(p)
        svg.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{width - 20}" y2="{y:.1f}" class="grid"/>')
        svg.append(f'<text x="{pad - 8}" y="{y + 4:.1f}" class="ax" text-anchor="end">${p:.2f}</text>')

    svg.append(f'<polygon points="{area}" fill="url(#g)"/>')
    svg.append(f'<polyline points="{line}" class="pline"/>')

    # moc chu ky
    for m in cfg.milestone_items():
        if not (t0 <= m["ts"] <= t1):
            continue
        x = X(m["ts"])
        svg.append(f'<line x1="{x:.1f}" y1="18" x2="{x:.1f}" y2="{height - pad}" class="mline"/>')
        svg.append(f'<text x="{x + 4:.1f}" y="14" class="mlab">{html.escape(m["date"][5:])}</text>')

    # nhan truc thoi gian
    for i in range(6):
        ts = t0 + (t1 - t0) * i / 5
        svg.append(f'<text x="{X(ts):.1f}" y="{height - pad + 18:.1f}" class="ax" '
                   f'text-anchor="middle">{config.fmt_ts(ts, False)[5:]}</text>')

    # marker mua/ban cua cac vi top
    top = [w["wallet"] for w in wallets[:8]]
    if top:
        qs = ",".join("?" * len(top))
        trades = conn.execute(
            f"SELECT wallet, ts, price, usd, side FROM trades WHERE wallet IN ({qs})"
            f" AND side IN ('BUY','SELL','WITHDRAW','DEPOSIT') AND price IS NOT NULL"
            f" AND usd IS NOT NULL ORDER BY usd DESC LIMIT 260", top).fetchall()
        for t in trades:
            if not (t0 <= t["ts"] <= t1):
                continue
            x, y = X(t["ts"]), Y(t["price"])
            r = max(2.5, min(11, (abs(t["usd"]) ** 0.5) / 26))
            buy = t["side"] in ("BUY", "WITHDRAW")
            cls = "mk-buy" if buy else "mk-sell"
            tip = (f'{"MUA" if buy else "BAN"} {_fmt(t["usd"], "usd")} @ ${t["price"]:.4f}'
                   f' - {config.fmt_ts(t["ts"])} - {t["wallet"][:10]}...')
            svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" class="{cls}">'
                       f'<title>{html.escape(tip)}</title></circle>')

    svg.append("</svg>")
    return "\n".join(svg)


# ---------------- cac phan cua trang ----------------

def _score_cell(row):
    detail = json.loads(row["score_json"]) if row["score_json"] else {}
    items = detail.get("hang_muc", {})
    bars = []
    for key, part in items.items():
        pct = (part["diem"] / part["toi_da"] * 100) if part["toi_da"] else 0
        bars.append(
            f'<div class="brow"><span class="bk">{html.escape(key.replace("_", " "))}</span>'
            f'<span class="btrack"><i style="width:{pct:.0f}%"></i></span>'
            f'<span class="bv">{part["diem"]:.2f}/{part["toi_da"]:.2f}</span></div>'
            f'<div class="bwhy">{html.escape(part["vi_sao"])}</div>')
    for pen in detail.get("phat", []):
        bars.append(f'<div class="pen">- {pen["tru"]:.2f} &middot; '
                    f'{html.escape(pen["vi_sao"])}</div>')
    return "".join(bars)


def _scan_url(wallet: str, contract: str) -> str:
    """Trang Etherscan liet ke dung cac giao dich UNI cua vi nay (khong phai toan bo token)."""
    return f"https://etherscan.io/token/{contract}?a={wallet}"


def _addr_link(wallet: str, contract: str, name: str = "") -> str:
    """Hien DAY DU 42 ky tu dia chi.

    Truoc day cat con 10 ky tu cho gon, nhung nhu vay Ctrl+F khong tim duoc va
    khong co cach nao lay dia chi ra de dung - trong khi dia chi chinh la thu duy nhat
    ban can de hanh dong. Do rong cot khong dang de danh doi lay dieu do.

    stopPropagation: bam vao dia chi thi mo Etherscan, bam cho khac trong dong thi
    bung/thu phan giai thich diem - hai hanh vi khong duoc dam nhau.
    """
    return (f'<span class="acell">'
            f'<a class="alink" href="{_scan_url(wallet, contract)}" target="_blank"'
            f' rel="noopener noreferrer" onclick="event.stopPropagation()"'
            f' title="Xem giao dich UNI cua vi nay tren Etherscan">'
            f'<code>{wallet}</code><span class="ext">↗</span></a>'
            f'<button class="copy" type="button" data-a="{wallet}"'
            f' onclick="cp(event,this)" title="Sao chep dia chi">⧉</button></span>'
            + (f'<div class="tag">{html.escape(name)}</div>' if name else ''))


def _whale_rows(rows, contract):
    out = []
    for i, r in enumerate(rows, 1):
        label, cls = band_of(r["copy_score"])
        out.append(f"""
<tr class="wrow" onclick="this.nextElementSibling.classList.toggle('open')">
  <td class="rank">{i}</td>
  <td>{_addr_link(r['wallet'], contract, r['name'] or '')}</td>
  <td><span class="pill {cls}">{r['copy_score']:.2f}</span><div class="tag">{label}</div></td>
  <td class="{'pos' if (r['total_pnl'] or 0) >= 0 else 'neg'}">{_fmt(r['total_pnl'], 'usd')}</td>
  <td>{_fmt(r['roi'], 'pct')}</td>
  <td>{_fmt(r['edge'], 'sig')}</td>
  <td>{r['round_trips'] or 0}</td>
  <td>{_fmt(r['uni_bought'], 'uni')}</td>
  <td>{_fmt(r['uni_sold'], 'uni')}</td>
  <td>{_fmt(r['balance_uni'], 'uni')}</td>
  <td>{_fmt(r['avg_cost'], 'price')}</td>
  <td class="muted">{config.fmt_ts(r['last_ts'], False)}</td>
</tr>
<tr class="detail"><td colspan="12"><div class="dbox">{_score_cell(r)}</div></td></tr>""")
    return "".join(out)


CSS = """
:root{--bg:#fbfbfd;--fg:#16181d;--muted:#6b7280;--card:#fff;--line:#e5e7eb;
--accent:#4f46e5;--pos:#047857;--neg:#b91c1c;--buy:#059669;--sell:#dc2626;
--s-a:#047857;--s-b:#0369a1;--s-c:#b45309;--s-d:#6b7280;--shadow:0 1px 3px rgba(0,0,0,.07)}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){
--bg:#0d0f14;--fg:#e8eaee;--muted:#98a2b3;--card:#161a22;--line:#262c38;
--accent:#818cf8;--pos:#34d399;--neg:#f87171;--buy:#34d399;--sell:#f87171;
--s-a:#34d399;--s-b:#60a5fa;--s-c:#fbbf24;--s-d:#98a2b3;--shadow:0 1px 3px rgba(0,0,0,.5)}}
:root[data-theme=dark]{--bg:#0d0f14;--fg:#e8eaee;--muted:#98a2b3;--card:#161a22;--line:#262c38;
--accent:#818cf8;--pos:#34d399;--neg:#f87171;--buy:#34d399;--sell:#f87171;
--s-a:#34d399;--s-b:#60a5fa;--s-c:#fbbf24;--s-d:#98a2b3;--shadow:0 1px 3px rgba(0,0,0,.5)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:27px;margin:0 0 6px;letter-spacing:-.02em}
h2{font-size:19px;margin:40px 0 12px;letter-spacing:-.01em}
.sub{color:var(--muted);margin:0 0 26px;font-size:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:18px;box-shadow:var(--shadow)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;margin-bottom:26px}
.kpi .v{font-size:23px;font-weight:650;letter-spacing:-.02em}
.kpi .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em;margin-top:3px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;min-width:960px;font-size:13.5px}
th{text-align:right;padding:9px 10px;border-bottom:2px solid var(--line);
color:var(--muted);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;
white-space:nowrap}
th:nth-child(-n+3),td:nth-child(-n+3){text-align:left}
td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
.wrow{cursor:pointer}.wrow:hover{background:color-mix(in srgb,var(--accent) 7%,transparent)}
.rank{color:var(--muted);font-variant-numeric:tabular-nums}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--fg)}
.acell{display:inline-flex;align-items:center;gap:2px}
.alink{text-decoration:none;display:inline-flex;align-items:center;gap:4px;border-radius:5px;
padding:1px 4px;margin:-1px 0 -1px -4px}
.alink code{color:var(--accent);text-decoration:underline;text-decoration-style:dotted;
text-underline-offset:2px;font-size:11.5px;letter-spacing:-.1px}
.alink .ext{color:var(--muted);font-size:10.5px;line-height:1}
.alink:hover{background:color-mix(in srgb,var(--accent) 15%,transparent)}
.alink:hover code{text-decoration-style:solid}
.alink:hover .ext{color:var(--accent)}
.alink:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.copy{border:1px solid var(--line);background:var(--card);color:var(--muted);cursor:pointer;
border-radius:5px;font-size:11px;line-height:1;padding:3px 5px;flex:0 0 auto}
.copy:hover{border-color:var(--accent);color:var(--accent)}
.copy.ok{border-color:var(--pos);color:var(--pos)}
.tag{color:var(--muted);font-size:11.5px;margin-top:2px;max-width:230px;
overflow:hidden;text-overflow:ellipsis}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;color:#fff;font-weight:650;font-size:13px}
.s-a{background:var(--s-a)}.s-b{background:var(--s-b)}.s-c{background:var(--s-c)}.s-d{background:var(--s-d)}
.pos{color:var(--pos)}.neg{color:var(--neg)}.muted{color:var(--muted)}
.detail{display:none}.detail.open{display:table-row}
.dbox{padding:14px 10px;background:color-mix(in srgb,var(--accent) 4%,transparent);
border-radius:9px;margin:6px 0;white-space:normal}
.brow{display:flex;align-items:center;gap:11px;margin-top:9px}
.bk{flex:0 0 118px;font-size:12.5px;font-weight:600;text-transform:capitalize}
.btrack{flex:1;height:7px;background:var(--line);border-radius:99px;overflow:hidden;min-width:90px}
.btrack i{display:block;height:100%;background:var(--accent);border-radius:99px}
.bv{flex:0 0 74px;text-align:right;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.bwhy{margin:2px 0 0 129px;font-size:12.5px;color:var(--muted)}
.pen{margin:9px 0 0 129px;font-size:12.5px;color:var(--neg)}
.chart{width:100%;height:auto;display:block}
.pline{fill:none;stroke:var(--accent);stroke-width:1.7}
.grid{stroke:var(--line);stroke-width:1}
.mline{stroke:var(--muted);stroke-width:1;stroke-dasharray:3 4;opacity:.65}
.mlab{fill:var(--muted);font-size:10.5px}
.ax{fill:var(--muted);font-size:10.5px}
.mk-buy{fill:var(--buy);fill-opacity:.62;stroke:var(--buy);stroke-width:1}
.mk-sell{fill:var(--sell);fill-opacity:.62;stroke:var(--sell);stroke-width:1}
.legend{display:flex;gap:18px;flex-wrap:wrap;color:var(--muted);font-size:12.5px;margin-top:10px}
.dot{display:inline-block;width:9px;height:9px;border-radius:99px;margin-right:5px;vertical-align:middle}
.warn{border-left:3px solid var(--s-c);padding-left:15px;margin:10px 0}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:14px 0}
.hero .v{font-size:26px;font-weight:680;letter-spacing:-.02em}
.hero .k{color:var(--muted);font-size:12px;margin-top:3px}
.st{display:inline-block;padding:2px 9px;border-radius:99px;font-weight:650;font-size:12px;color:#fff}
.st-buy{background:var(--pos)}.st-sell{background:var(--neg)}
.st-hold{background:var(--s-b)}.st-out{background:var(--s-d)}
.dir{font-weight:650}.d-up{color:var(--pos)}.d-dn{color:var(--neg)}.d-nt{color:var(--muted)}
.gauge{height:12px;border-radius:99px;background:linear-gradient(90deg,var(--neg),var(--muted),var(--pos));
position:relative;margin:14px 0 6px}
.gauge i{position:absolute;top:-4px;width:4px;height:20px;background:var(--fg);border-radius:2px;
box-shadow:0 0 0 2px var(--card)}
.why{color:var(--muted);font-size:12.5px;margin-top:3px;white-space:normal;max-width:640px}
.plan{border-left:3px solid var(--accent);padding-left:15px;margin:14px 0}
.plan h3{margin:0 0 6px;font-size:15px}
.lvl{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0}
.lvl div{background:color-mix(in srgb,var(--accent) 8%,transparent);border-radius:8px;padding:9px 13px;font-size:13px}
ul.lim{margin:8px 0;padding-left:20px;color:var(--muted);font-size:13.5px}
ul.lim li{margin:6px 0}
"""


def build_html(cfg, conn, whales, mms, stats, entry_price=None) -> str:
    chart = _price_chart(conn, cfg, whales)
    ms_rows = "".join(
        f"<tr><td>{html.escape(m['date'])}</td><td>{_fmt(m['price'], 'price')}</td>"
        f"<td style='text-align:left'>{html.escape(m['label'])}</td></tr>"
        for m in cfg.milestone_items())

    contract = cfg["token"]["contract"]
    mm_rows = "".join(f"""
<tr><td>{_addr_link(r['wallet'], contract, r['name'] or '')}</td>
  <td>{html.escape(r['klass'] or '')}</td>
  <td>{r['mm_score']:.2f}</td>
  <td>{_fmt(r['n_trades'], 'num')}</td>
  <td>{_fmt(r['gross_uni'], 'uni')}</td>
  <td>{_fmt(r['net_uni'], 'uni')}</td></tr>""" for r in mms)

    price_now = stats.get("price_now") or 0.0
    entry = entry_price or 3.3
    since_ts = config.parse_when(
        (cfg.get("milestones", {}).get("correction_low", {}) or {}).get("date", "2026-08-14"))
    stance_from = config.fmt_ts(since_ts, False)
    lv = rs.levels(conn, price_now)
    plan_html = rs.plan_section(conn, price_now, entry, lv)
    stance_html = rs.stance_section(conn, cfg, since_ts, price_now, contract, _addr_link)
    signals_html = rs.signals_section(conn, price_now, since_ts)
    resist_html = rs.resistance_section(conn, price_now, entry)
    hero_html = rs.hero(conn, price_now, entry, cfg.milestone_items())

    trunc = stats.get("truncated", 0)
    warn = ""
    if trunc:
        warn = (f"<div class='card warn'><b>{trunc} block khong lay du duoc log.</b> "
                f"Ket qua cua cac block do co the thieu. Xem bang <code>truncated_blocks</code>.</div>")

    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>UNI Whale Tracker</title><style>{CSS}</style></head><body><div class="wrap">

<h1>UNI Whale Tracker</h1>
<p class="sub">Truy vet vi tich gop day &rarr; xa dinh cho chu ky
{config.fmt_ts(cfg.since_ts(), False)} &rarr; {config.fmt_ts(cfg.until_ts(), False)}.
Tao luc {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC.
Khong dung AI &middot; Khong dung API key tra phi.</p>

{hero_html}

<div class="kpis">
  <div class="card kpi"><div class="v">{stats['transfers']:,}</div><div class="k">transfer on-chain</div></div>
  <div class="card kpi"><div class="v">{stats['addresses']:,}</div><div class="k">dia chi</div></div>
  <div class="card kpi"><div class="v">{stats['n_whales']:,}</div><div class="k">vi whale ung vien</div></div>
  <div class="card kpi"><div class="v">{stats['n_good']:,}</div><div class="k">vi dat &ge; 3.0 diem</div></div>
  <div class="card kpi"><div class="v">{_fmt(stats['price_now'], 'price')}</div><div class="k">gia UNI hien tai</div></div>
</div>
{warn}

<h2>Danh muc cua ban</h2>
{plan_html}

<h2>Cac vi dang lam gi o vung gia nay</h2>
<p class="sub" style="margin:-4px 0 10px">Tinh tu {stance_from} den nay &mdash; giai doan gia
chay tu day len dinh. Day la cau tra loi cho cau hoi "ho mua tiep, xa, hay van giu".</p>
{stance_html}

<h2>Vung khang cu &mdash; gia se vap phai gi o phia tren</h2>
{resist_html}

<h2>Gia con tang duoc nua khong &mdash; can theo chi so</h2>
<p class="sub" style="margin:-4px 0 10px">Moi chi so la mot phep dem tren du lieu that, co kem
cach doc de ban tu kiem chung. <b>Day la do ap luc dang co, khong phai loi tien tri.</b></p>
{signals_html}

<h2>Gia UNI &amp; hanh dong cua whale</h2>
<div class="card">{chart}
<div class="legend">
  <span><i class="dot" style="background:var(--buy)"></i>Mua / rut khoi CEX</span>
  <span><i class="dot" style="background:var(--sell)"></i>Ban / nap len CEX</span>
  <span>Kich thuoc cham = gia tri USD &middot; Duong dut = moc chu ky &middot; Di chuot vao cham de xem chi tiet</span>
</div></div>

<h2>Moc chu ky</h2>
<div class="card scroll"><table style="min-width:520px">
<thead><tr><th>Ngay</th><th>Gia</th><th style="text-align:left">Su kien</th></tr></thead>
<tbody>{ms_rows}</tbody></table></div>

<h2>Bang xep hang vi whale</h2>
<p class="sub" style="margin:-4px 0 12px">Bam vao <b>dia chi vi</b> de mo trang giao dich UNI cua vi do tren Etherscan. Bam vao <b>cho khac trong dong</b> de xem tai sao vi do duoc diem nhu vay.
Thang diem: <b>&ge;4.0</b> copy tu tin &middot; <b>3.0&ndash;3.9</b> copy size nho &middot;
<b>2.0&ndash;2.9</b> chi theo doi &middot; <b>&lt;2.0</b> bo qua.</p>
<div class="card scroll"><table>
<thead><tr><th>#</th><th>Vi</th><th>Diem</th><th>PnL</th><th>ROI</th><th>Edge</th>
<th>Vong</th><th>Da mua</th><th>Da ban</th><th>Con lai</th><th>Gia von</th><th>Lan cuoi</th></tr></thead>
<tbody>{_whale_rows(whales, contract)}</tbody></table></div>

<h2>Ai lai gia &mdash; Market Maker &amp; MEV</h2>
<p class="sub" style="margin:-4px 0 12px">Nhung vi nay <b>khong phai de copy</b>. Chung giu ton kho
gan nhu khong doi va giao dich lien tuc 2 chieu &mdash; do la dieu tiet thanh khoan, khong phai dat cuoc huong gia.</p>
<div class="card scroll"><table style="min-width:700px">
<thead><tr><th>Vi</th><th>Loai</th><th>Diem MM</th><th>So lenh</th><th>Tong volume</th><th>Dong rong</th></tr></thead>
<tbody>{mm_rows}</tbody></table></div>

<h2>Doc ky truoc khi dat tien</h2>
<div class="card"><ul class="lim">
<li><b>Chuyen token len CEX khong co nghia la da ban.</b> Do chi la suy doan (do tin cay 0.70).
Vi co the chi dang chuyen kho.</li>
<li><b>Tool khong nhin thay giao dich trong san.</b> Whale chu yeu trade tren Binance thi vo hinh voi tool nay.</li>
<li><b>Copy-trade luon tre.</b> Tu luc giao dich len block den luc ban vao lenh, gia da chay.
Vi vay hang muc "copy duoc" uu tien vi gom tu tu nhieu gio, khong uu tien vi ban 1 phat.</li>
<li><b>Diem 5/5 nghia la "qua khu rat gioi", khong phai "chac chan thang".</b></li>
<li><b>Gia von cua phan ton kho co truoc cua so phan tich la uoc luong</b> &mdash; cot "do tin cay gia von"
trong file CSV cho biet muc do chac chan cua tung vi.</li>
</ul></div>

</div>
<script>
// Sao chep dia chi. Bao cao thuong duoc mo bang file:// - o do navigator.clipboard
// bi trinh duyet chan vi khong phai "secure context", nen phai co duong lui.
function cp(e, btn) {{
  e.stopPropagation();
  var addr = btn.getAttribute('data-a');
  var done = function () {{
    var old = btn.textContent;
    btn.textContent = '\\u2713';
    btn.classList.add('ok');
    setTimeout(function () {{ btn.textContent = old; btn.classList.remove('ok'); }}, 1200);
  }};
  if (navigator.clipboard && window.isSecureContext) {{
    navigator.clipboard.writeText(addr).then(done, function () {{ fallback(addr, done); }});
  }} else {{
    fallback(addr, done);
  }}
}}
function fallback(text, done) {{
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try {{ document.execCommand('copy'); done(); }} catch (err) {{ window.prompt('Sao chep dia chi:', text); }}
  document.body.removeChild(ta);
}}
</script>
</body></html>"""


# ---------------- dieu phoi ----------------

def run(cfg, conn, top=40, verbose=True, entry_price=None) -> dict:
    price_now = conn.execute("SELECT close FROM prices ORDER BY ts DESC LIMIT 1").fetchone()
    price_now = price_now["close"] if price_now else None

    whales = conn.execute("""
        SELECT * FROM wallet_stats
        WHERE klass IN ('whale_candidate','fund','retail') AND n_trades > 0
        ORDER BY copy_score DESC, total_pnl DESC LIMIT ?""", (top,)).fetchall()
    mms = conn.execute("""
        SELECT * FROM wallet_stats WHERE mm_score >= 2.5 OR klass IN ('market_maker','mev_bot','cex_shuttle')
        ORDER BY mm_score DESC LIMIT 25""").fetchall()

    stats = {
        "transfers": conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0],
        "addresses": conn.execute("SELECT COUNT(*) FROM addresses").fetchone()[0],
        "n_whales": conn.execute(
            "SELECT COUNT(*) FROM addresses WHERE klass='whale_candidate'").fetchone()[0],
        "n_good": conn.execute(
            "SELECT COUNT(*) FROM wallet_stats WHERE copy_score>=3"
            " AND klass IN ('whale_candidate','fund','retail')").fetchone()[0],
        "truncated": conn.execute("SELECT COUNT(*) FROM truncated_blocks").fetchone()[0],
        "price_now": price_now,
    }

    out = config.OUT_DIR
    min_score = float(cfg["monitor"]["min_score_to_watch"])
    (out / "report.html").write_text(
        build_html(cfg, conn, whales, mms, stats, entry_price), encoding="utf-8")

    # CSV de loc trong Excel
    cols = [d[0] for d in conn.execute("SELECT * FROM wallet_stats LIMIT 1").description
            if d[0] != "score_json"]
    with open(out / "whales.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in conn.execute(
                "SELECT * FROM wallet_stats ORDER BY copy_score DESC").fetchall():
            w.writerow([r[c] for c in cols])

    with open(out / "trades.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["wallet", "klass", "copy_score", "ts_utc", "side", "amount_uni",
                    "price_usd", "value_usd", "confidence", "counterparty", "cp_class", "tx_hash"])
        # Chi xuat lenh cua cac vi dang chu y. Xuat het 900k+ lenh se ra file 200MB
        # ma Excel khong mo noi - vo dung. Toan bo van con trong SQLite neu can dao sau.
        for r in conn.execute("""
                SELECT t.*, s.klass, s.copy_score FROM trades t
                JOIN wallet_stats s ON s.wallet = t.wallet
                WHERE t.side <> 'INTERNAL' AND s.copy_score >= ?
                  AND s.klass IN ('whale_candidate','fund','retail')
                ORDER BY s.copy_score DESC, t.ts""",
                (min(min_score, 2.0),)).fetchall():
            w.writerow([r["wallet"], r["klass"], r["copy_score"], config.fmt_ts(r["ts"]),
                        r["side"], r["amount"], r["price"], r["usd"], r["conf"],
                        r["counterparty"], r["cp_klass"], r["tx_hash"]])

    # watchlist cho module monitor
    watch = [{
        "wallet": r["wallet"], "score": r["copy_score"], "name": r["name"],
        "klass": r["klass"], "balance_uni": r["balance_uni"], "avg_cost": r["avg_cost"],
        "total_pnl": r["total_pnl"], "roi": r["roi"], "edge": r["edge"],
        "round_trips": r["round_trips"],
    } for r in conn.execute("""
        SELECT * FROM wallet_stats WHERE copy_score >= ?
          AND klass IN ('whale_candidate','fund','retail')
        ORDER BY copy_score DESC""", (min_score,)).fetchall()]
    (out / "watchlist.json").write_text(json.dumps({
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "token": cfg["token"], "min_score": min_score, "wallets": watch,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    if verbose:
        print(f"[report] out/report.html    ({top} vi hang dau, bam vao dong de xem breakdown)")
        print(f"[report] out/whales.csv     ({conn.execute('SELECT COUNT(*) FROM wallet_stats').fetchone()[0]:,} vi)")
        print(f"[report] out/trades.csv")
        print(f"[report] out/watchlist.json ({len(watch)} vi dat >= {min_score} diem)")
        if stats["truncated"]:
            print(f"[report] CANH BAO: {stats['truncated']} block thieu du lieu, da ghi vao bao cao")
    return {"whales": len(whales), "watchlist": len(watch)}
