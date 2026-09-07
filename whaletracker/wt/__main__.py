"""CLI: python -m wt <lenh>

Chay lan dau, theo thu tu:
    python -m wt ingest        # nap 25 file CSV lam seed
    python -m wt backfill       # keo on-chain + gia (lau nhat, ~10 phut, resume duoc)
    python -m wt classify       # phan loai dia chi
    python -m wt ledger         # dung so mua/ban + PnL
    python -m wt score          # cham diem copy-trade /5
    python -m wt report         # xuat HTML + CSV + watchlist.json
Hoac gop lai:
    python -m wt all
"""
from __future__ import annotations

import argparse
import sys

from . import config, db

# Console Windows mac dinh la cp1252, khong in duoc emoji trong canh bao.
# Ep UTF-8 ngay tu dau thay vi bo emoji - Linux/Oracle khong bi anh huong.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _conn_cfg(args):
    cfg = config.load(getattr(args, "config", None))
    if getattr(args, "since", None):
        cfg["window"]["since"] = args.since
    if getattr(args, "until", None):
        cfg["window"]["until"] = args.until
    return cfg, db.connect(getattr(args, "db", None))


def cmd_ingest(args):
    from . import ingest_csv
    cfg, conn = _conn_cfg(args)
    ingest_csv.run(cfg, conn, args.csv_dir)
    return 0


def cmd_backfill(args):
    from . import backfill
    cfg, conn = _conn_cfg(args)
    backfill.run(cfg, conn, dry_run=args.dry_run, skip_deep=args.skip_deep)
    return 0


def cmd_refresh(args):
    from . import refresh
    cfg, conn = _conn_cfg(args)
    refresh.run(cfg, conn, top_n=args.top_n)
    return 0


def cmd_classify(args):
    from . import classify
    cfg, conn = _conn_cfg(args)
    classify.run(cfg, conn)
    return 0


def cmd_ledger(args):
    from . import ledger
    cfg, conn = _conn_cfg(args)
    ledger.run(cfg, conn)
    return 0


def cmd_score(args):
    from . import score
    cfg, conn = _conn_cfg(args)
    score.run(cfg, conn)
    return 0


def cmd_report(args):
    from . import report
    cfg, conn = _conn_cfg(args)
    report.run(cfg, conn, top=args.top, entry_price=args.entry)
    return 0


def cmd_all(args):
    from . import backfill, classify, ingest_csv, ledger, report, score
    cfg, conn = _conn_cfg(args)
    steps = [
        ("INGEST", lambda: ingest_csv.run(cfg, conn, args.csv_dir)),
        ("BACKFILL", lambda: backfill.run(cfg, conn, skip_deep=args.skip_deep)),
        ("CLASSIFY", lambda: classify.run(cfg, conn)),
        ("LEDGER", lambda: ledger.run(cfg, conn)),
        ("SCORE", lambda: score.run(cfg, conn)),
        ("REPORT", lambda: report.run(cfg, conn, top=args.top)),
    ]
    for i, (name, fn) in enumerate(steps, 1):
        print(f"\n{'=' * 62}\n  BUOC {i}/{len(steps)}: {name}\n{'=' * 62}")
        fn()
    return 0


def cmd_status(args):
    _, conn = _conn_cfg(args)
    print(f"DB: {config.DATA_DIR / 'whale.db'}")
    for key, value in db.counts(conn).items():
        print(f"  {key:12s} {value:>12,}")
    row = conn.execute("SELECT MIN(ts) lo, MAX(ts) hi FROM transfers WHERE ts IS NOT NULL").fetchone()
    if row and row["lo"]:
        print(f"  transfer tu {config.fmt_ts(row['lo'])} den {config.fmt_ts(row['hi'])}")
    row = conn.execute("SELECT MIN(ts) lo, MAX(ts) hi FROM prices").fetchone()
    if row and row["lo"]:
        print(f"  gia      tu {config.fmt_ts(row['lo'])} den {config.fmt_ts(row['hi'])}")
    rows = conn.execute(
        "SELECT klass, COUNT(*) n FROM addresses WHERE klass IS NOT NULL"
        " GROUP BY klass ORDER BY n DESC").fetchall()
    if rows:
        print("  phan loai:")
        for r in rows:
            print(f"    {r['klass']:18s} {r['n']:>6,}")
    return 0


def cmd_monitor(args):
    from . import monitor
    cfg, conn = _conn_cfg(args)
    return monitor.run(cfg, conn, test_alert=args.test_alert,
                       replay=args.replay, once=args.once)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="wt", description="UNI Whale Tracker - truy vet vi whale, cham diem copy-trade")
    parser.add_argument("--config", help="duong dan config.json")
    parser.add_argument("--db", help="duong dan file SQLite")
    parser.add_argument("--since", help="ghi de moc bat dau, vd 2026-04-01")
    parser.add_argument("--until", help="ghi de moc ket thuc, vd now")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="nap CSV lam seed")
    p.add_argument("--csv-dir")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("backfill", help="keo on-chain + gia")
    p.add_argument("--dry-run", action="store_true", help="chi in ke hoach, khong goi mang")
    p.add_argument("--skip-deep", action="store_true", help="bo qua dao sau tung vi")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("refresh", help="cap nhat lich su vi theo doi qua Ethplorer (khong can key)")
    p.add_argument("--top-n", type=int, default=150)
    p.set_defaults(func=cmd_refresh)

    sub.add_parser("classify", help="phan loai dia chi").set_defaults(func=cmd_classify)
    sub.add_parser("ledger", help="dung so mua/ban + PnL").set_defaults(func=cmd_ledger)
    sub.add_parser("score", help="cham diem copy-trade /5").set_defaults(func=cmd_score)

    p = sub.add_parser("report", help="xuat HTML + CSV")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--entry", type=float, default=3.3,
                   help="gia von danh muc cua ban, de bao cao tinh lai/lo (mac dinh 3.3)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("all", help="chay tat ca cac buoc")
    p.add_argument("--csv-dir")
    p.add_argument("--skip-deep", action="store_true")
    p.add_argument("--top", type=int, default=40)
    p.set_defaults(func=cmd_all)

    sub.add_parser("status", help="xem tinh trang DB").set_defaults(func=cmd_status)

    p = sub.add_parser("monitor", help="theo doi realtime + canh bao")
    p.add_argument("--test-alert", action="store_true", help="gui 1 canh bao thu")
    p.add_argument("--replay", help="phat lai 1 ngay qua khu, vd 2026-07-31")
    p.add_argument("--once", action="store_true", help="quet 1 lan roi thoat")
    p.set_defaults(func=cmd_monitor)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n[wt] da dung. Chay lai lenh cu de tiep tuc (moi buoc deu resume duoc).")
        return 130


if __name__ == "__main__":
    sys.exit(main())
