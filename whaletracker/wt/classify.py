"""Buoc 3 - phan loai dia chi.

Hai lop:
  1. Khop nametag (Etherscan tu CSV + public_tags tu Blockscout) -> chac chan nhat
  2. Heuristic thong ke cho 421 vi chua co tag -> whale that thuong nam o day

Sau do gom vi cung chu thanh cluster bang union-find, de giao dich noi bo
(vi A -> vi B cung chu) khong bi tinh nham la mua/ban.
"""
from __future__ import annotations

import json
from collections import defaultdict

from . import db, known

# Thu tu uu tien: nhan o tren thang nhan o duoi.
PRIORITY = ["burn", "token_contract", "cex", "cex_shuttle", "dex_pool", "router", "bridge",
            "mev_bot", "market_maker", "protocol", "fund", "whale_candidate",
            "contract_other", "retail"]


# Moi transfer sinh ra 2 dong: 1 cho ben nhan, 1 cho ben gui.
# Tong hop bang SQL chu khong bang dict/set Python: du lieu that co ~600k transfer,
# gom counterparty vao set trong RAM se ngon hang tram MB va de OOM tren VM free tier.
_SIDES = """
    SELECT to_addr AS addr, from_addr AS cp, amount AS amt_in, 0.0 AS amt_out,
           1 AS n_in, 0 AS n_out, ts FROM transfers
    UNION ALL
    SELECT from_addr, to_addr, 0.0, amount, 0, 1, ts FROM transfers
"""


def _agg_stats(conn) -> dict[str, dict]:
    """Tong hop dong tien / tan suat / do phu thoi gian cho tung dia chi (bang SQL)."""
    stats: dict[str, dict] = {}
    for r in conn.execute(f"""
        SELECT addr,
               SUM(amt_in) in_amt, SUM(amt_out) out_amt,
               SUM(n_in) n_in, SUM(n_out) n_out,
               COUNT(DISTINCT cp) n_cp,
               COUNT(DISTINCT ts / 3600) n_hours,
               MIN(ts) first_ts, MAX(ts) last_ts
        FROM ({_SIDES}) GROUP BY addr"""):
        s = dict(r)
        s["gross"] = s["in_amt"] + s["out_amt"]
        s["net"] = s["in_amt"] - s["out_amt"]
        s["n_tx"] = s["n_in"] + s["n_out"]
        s["net_ratio"] = abs(s["net"]) / s["gross"] if s["gross"] > 0 else 0.0
        biggest = max(s["in_amt"], s["out_amt"])
        s["io_ratio"] = (min(s["in_amt"], s["out_amt"]) / biggest) if biggest > 0 else 0.0
        stats[r["addr"]] = s
    return stats


def _atomic_ratio(conn) -> dict[str, float]:
    """Ty le giao dich ma dia chi vua nhan vua gui trong CUNG 1 tx.

    Day la dau van tay cua MEV bot / arbitrageur: token chi di qua vi trong 1 khoanh khac.
    """
    return {r["addr"]: r["ratio"] for r in conn.execute(f"""
        SELECT addr,
               SUM(CASE WHEN got_in > 0 AND got_out > 0 THEN 1 ELSE 0 END) * 1.0
                 / COUNT(*) AS ratio
        FROM (
            SELECT tx_hash, addr, MAX(n_in) got_in, MAX(n_out) got_out
            FROM (SELECT tx_hash, to_addr AS addr, 1 n_in, 0 n_out FROM transfers
                  UNION ALL
                  SELECT tx_hash, from_addr, 0, 1 FROM transfers)
            GROUP BY tx_hash, addr
        ) GROUP BY addr""")}


def _label_text(conn) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in conn.execute("SELECT addr, label FROM labels").fetchall():
        out[r["addr"]] = (out.get(r["addr"], "") + " | " + r["label"]).strip(" |")
    for r in conn.execute("SELECT addr, name FROM addresses WHERE name IS NOT NULL").fetchall():
        out[r["addr"]] = (out.get(r["addr"], "") + " | " + r["name"]).strip(" |")
    return out


def _classify_one(addr, stats, labels, atomic, is_contract, cfg) -> tuple[str, str]:
    th = cfg["thresholds"]

    if addr in known.FIXED:
        return known.FIXED[addr], "dia chi co dinh da biet"

    tag = labels.get(addr)
    hit = known.match_label(tag)
    if hit:
        klass, needle = hit
        # "Binance Dep: 0x..." la vi nap tien cua nguoi dung -> van thuoc CEX
        if known.is_deposit_tag(tag) and klass == "cex":
            return "cex", f"nametag vi nap CEX: '{needle}'"
        return klass, f"nametag khop '{needle}'"

    s = stats.get(addr)
    if not s or s["n_tx"] == 0:
        return "retail", "khong co giao dich trong cua so"

    # --- heuristic cho vi chua co nametag ---

    if atomic.get(addr, 0) >= th["mev_same_block_ratio"] and s["n_tx"] >= 10:
        return "mev_bot", f"{atomic[addr]:.0%} giao dich vao-ra cung 1 tx (dau van tay MEV)"

    if is_contract:
        if (s["n_cp"] >= th["dex_pool_min_counterparties"]
                and 0.7 <= s["io_ratio"] <= 1.0):
            return "dex_pool", f"contract, {s['n_cp']} doi tac, dong vao/ra can bang ({s['io_ratio']:.2f})"
        if s["n_cp"] >= 50 and s["n_tx"] >= 100:
            return "router", f"contract, {s['n_cp']} doi tac, {s['n_tx']} giao dich"

    hour_span = ((s["last_ts"] - s["first_ts"]) / 3600) if (s["last_ts"] and s["first_ts"]) else 0
    coverage = (s["n_hours"] / hour_span) if hour_span > 1 else 0.0
    if (s["net_ratio"] < th["mm_net_ratio_max"]
            and s["n_tx"] >= th["mm_min_transfers"]
            and coverage >= th["mm_min_hour_coverage"]):
        return "market_maker", (f"ton kho gan nhu khong doi (net/gross={s['net_ratio']:.1%}), "
                                f"{s['n_tx']} giao dich, hoat dong {coverage:.0%} so gio")

    if is_contract:
        return "contract_other", "contract khong khop mau nao"

    if s["gross"] >= th["whale_min_volume_uni"]:
        return "whale_candidate", f"EOA, tong volume {s['gross']:,.0f} UNI"

    return "retail", f"EOA nho, volume {s['gross']:,.0f} UNI"


# ---------------- gom vi cung chu ----------------

class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_clusters(conn, cfg, klass_map: dict[str, str]) -> dict[str, str]:
    """Noi 2 EOA lai neu chung chuyen UNI truc tiep cho nhau va dong rong gan nhu bang 0
    (dau hieu dieu chuyen noi bo giua cac vi cung chu, khong phai mua ban).
    """
    tol = float(cfg["thresholds"]["cluster_net_tolerance"])
    eoa = {a for a, k in klass_map.items() if k in ("whale_candidate", "retail")}
    if not eoa:
        return {}

    # Gom cap dia chi ngay trong SQL. Chuan hoa cap theo thu tu chu cai truoc khi
    # GROUP BY de A->B va B->A roi vao cung mot nhom.
    pairs: dict[tuple, dict] = defaultdict(lambda: {"a2b": 0.0, "b2a": 0.0, "n": 0})
    for r in conn.execute("""
        SELECT MIN(from_addr, to_addr) a, MAX(from_addr, to_addr) b,
               SUM(CASE WHEN from_addr < to_addr THEN amount ELSE 0 END) a2b,
               SUM(CASE WHEN from_addr > to_addr THEN amount ELSE 0 END) b2a,
               COUNT(*) n
        FROM transfers WHERE from_addr <> to_addr
        GROUP BY a, b"""):
        if r["a"] in eoa and r["b"] in eoa:
            pairs[(r["a"], r["b"])] = {"a2b": r["a2b"], "b2a": r["b2a"], "n": r["n"]}

    uf = _UnionFind()
    for (a, b), flow in pairs.items():
        gross = flow["a2b"] + flow["b2a"]
        if gross <= 0:
            continue
        net_ratio = abs(flow["a2b"] - flow["b2a"]) / gross
        # Gom khi (a) co dong 2 chieu gan can bang, hoac (b) chuyen 1 chieu lap lai nhieu lan
        if (flow["a2b"] > 0 and flow["b2a"] > 0 and net_ratio <= max(tol, 0.15)) or flow["n"] >= 5:
            uf.union(a, b)

    clusters: dict[str, str] = {}
    for addr in eoa:
        root = uf.find(addr)
        if root != addr or any(uf.find(o) == root for o in eoa if o != addr):
            clusters[addr] = root
    return clusters


# ---------------- dieu phoi ----------------

def reclass_cex_shuttles(conn, results: dict[str, tuple], stats: dict) -> int:
    """Luot 2: tach ví van hanh cua san ra khoi danh sach whale.

    Da thay trong du lieu that: nhung dia chi luan chuyen 9-11 TRIEU UNI nhung dong tien
    di 100% qua CEX, khong mot lenh DEX nao, do dut khoat chi 7-8%. Do la vi noi bo cua
    san / ban OTC / custody - chung khong dat cuoc vao gia, nen khong the copy-trade theo.
    Chung dang bi xep nham la 'whale_candidate' vi la EOA co volume lon.

    Phai lam o luot 2 vi can biet nhan cua doi tac, ma nhan do chinh la ket qua luot 1.
    """
    cex_addrs = {a for a, (k, _) in results.items() if k == "cex"}
    if not cex_addrs:
        return 0

    flows: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])  # [qua_cex, tong]
    for r in conn.execute(
            "SELECT from_addr, to_addr, amount FROM transfers WHERE amount > 0"):
        for addr, cp in ((r["to_addr"], r["from_addr"]), (r["from_addr"], r["to_addr"])):
            slot = flows[addr]
            slot[1] += r["amount"]
            if cp in cex_addrs:
                slot[0] += r["amount"]

    n = 0

    # --- Luat 1: dong tien gan nhu chi di qua CEX ---
    for addr, (klass, _) in list(results.items()):
        if klass != "whale_candidate":
            continue
        via_cex, total = flows.get(addr, (0.0, 0.0))
        st = stats.get(addr) or {}
        if total <= 0 or st.get("n_tx", 0) < 20:
            continue
        cex_ratio = via_cex / total
        conviction = st.get("net_ratio", 1.0)
        if cex_ratio >= 0.90 and conviction < 0.15:
            results[addr] = ("cex_shuttle",
                             f"{cex_ratio:.0%} dong tien chi qua CEX, do dut khoat chi "
                             f"{conviction:.0%} -> vi van hanh cua san, khong phai nguoi dat cuoc gia")
            n += 1

    # --- Luat 2: volume khong lo nhung chi giao dich voi vai dia chi ---
    # Nguoi giao dich that phai cham pool/router/nhieu doi tac. Mot dia chi luan chuyen
    # hang chuc trieu UNI ma chi qua lai voi 2-3 dia chi la DUONG ONG cua san, khong phai trader.
    # Da gap that: 0x5a52e96bac luan chuyen 73 trieu UNI qua dung 2 dia chi
    # (Binance kho lanh -> Binance vi nong) va suyt bi bao nham la "whale xa 2 trieu UNI".
    for addr, (klass, _) in list(results.items()):
        if klass != "whale_candidate":
            continue
        st = stats.get(addr) or {}
        if st.get("gross", 0) >= 5_000_000 and 0 < st.get("n_cp", 99) <= 3:
            results[addr] = ("cex_shuttle",
                             f"luan chuyen {st['gross']:,.0f} UNI nhung chi qua {st['n_cp']} dia chi "
                             f"-> duong ong noi bo cua san, khong phai nguoi giao dich")
            n += 1
    return n


def run(cfg, conn, verbose=True) -> dict:
    stats = _agg_stats(conn)
    labels = _label_text(conn)
    atomic = _atomic_ratio(conn)
    contract_flag = {r["addr"]: r["is_contract"] for r in
                     conn.execute("SELECT addr, is_contract FROM addresses").fetchall()}

    all_addrs = set(stats) | set(contract_flag) | set(labels)
    db.touch_addresses(conn, all_addrs)

    results = {}
    for addr in all_addrs:
        klass, reason = _classify_one(
            addr, stats, labels, atomic, bool(contract_flag.get(addr)), cfg)
        results[addr] = (klass, reason)

    n_shuttle = reclass_cex_shuttles(conn, results, stats)
    clusters = build_clusters(conn, cfg, {a: k for a, (k, _) in results.items()})

    conn.executemany(
        "UPDATE addresses SET klass=?, klass_reason=?, cluster_id=?, first_ts=?, last_ts=? WHERE addr=?",
        [(k, r, clusters.get(a), (stats.get(a) or {}).get("first_ts"),
          (stats.get(a) or {}).get("last_ts"), a)
         for a, (k, r) in results.items()],
    )
    conn.commit()

    tally: dict[str, int] = defaultdict(int)
    for k, _ in results.values():
        tally[k] += 1
    n_clusters = len(set(clusters.values()))

    if verbose:
        print(f"[classify] phan loai {len(results):,} dia chi")
        for klass in PRIORITY:
            if tally.get(klass):
                print(f"    {klass:18s} {tally[klass]:>6,}")
        print(f"[classify] gom duoc {n_clusters} cluster tu {len(clusters)} vi cung chu")
        if n_shuttle:
            print(f"[classify] tach {n_shuttle} vi van hanh san (cex_shuttle) khoi danh sach whale")

        # Moc kiem chung co dinh - lech la biet heuristic hong
        checks = [
            ("0x51c72848c68a965f66fa7a88855f9f7784502a7f", "market_maker"),
            ("0xf7cb79d787e0fe2b20e5cf7ed06986712d8008a9", "whale_candidate"),
        ]
        for addr, want in checks:
            got = results.get(addr, ("(khong thay)", ""))[0]
            mark = "OK " if got == want else "SAI"
            print(f"    [{mark}] {addr[:10]}... mong doi {want}, nhan {got}")

    return {"total": len(results), "tally": dict(tally), "clusters": n_clusters}
