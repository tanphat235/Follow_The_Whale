"""Doc config.json, cho phep ghi de bang bien moi truong WT_*.

Bien moi truong duoc uu tien cao hon file -> deploy len Oracle Cloud thi de secret
trong /etc/whaletracker.env, khong bao gio commit vao code.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"
CSV_DIR = ROOT.parent / "260731_data_transfer"

# Bien moi truong -> duong dan trong config. Chi map nhung thu la secret hoac hay doi.
ENV_MAP = {
    "WT_ETHERSCAN_KEY": ("sources", "etherscan_key"),
    "WT_BLOCKSCOUT_BASE": ("sources", "blockscout_base"),
    "WT_EMAIL_ENABLED": ("alerts", "email", "enabled"),
    "WT_EMAIL_USERNAME": ("alerts", "email", "username"),
    "WT_EMAIL_APP_PASSWORD": ("alerts", "email", "app_password"),
    "WT_EMAIL_TO": ("alerts", "email", "to"),
    "WT_EMAIL_SMTP_HOST": ("alerts", "email", "smtp_host"),
    "WT_EMAIL_SMTP_PORT": ("alerts", "email", "smtp_port"),
    "WT_TELEGRAM_ENABLED": ("alerts", "telegram", "enabled"),
    "WT_TELEGRAM_TOKEN": ("alerts", "telegram", "bot_token"),
    "WT_TELEGRAM_CHAT_ID": ("alerts", "telegram", "chat_id"),
    "WT_POLL_SEC": ("monitor", "poll_sec"),
    "WT_MIN_SCORE": ("monitor", "min_score_to_watch"),
    "WT_SINCE": ("window", "since"),
    "WT_UNTIL": ("window", "until"),
    "WT_RATE_LIMIT": ("http", "rate_limit_per_sec"),
}

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}


def _coerce(old: Any, raw: str) -> Any:
    """Ep kieu gia tri env theo kieu cua gia tri goc trong config.json."""
    if isinstance(old, bool):
        low = raw.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"gia tri boolean khong hop le: {raw!r}")
    if isinstance(old, int) and not isinstance(old, bool):
        return int(raw)
    if isinstance(old, float):
        return float(raw)
    return raw


def _set_path(cfg: dict, path: tuple, value: Any) -> None:
    node = cfg
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def _get_path(cfg: dict, path: tuple) -> Any:
    node: Any = cfg
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


class Config(dict):
    """dict thuong, them vai helper hay dung."""

    @property
    def contract(self) -> str:
        return self["token"]["contract"].lower()

    @property
    def decimals(self) -> int:
        return int(self["token"]["decimals"])

    def since_ts(self) -> int:
        return parse_when(self["window"]["since"])

    def until_ts(self) -> int:
        return parse_when(self["window"]["until"])

    def milestone_items(self) -> list[dict]:
        out = []
        for key, m in self.get("milestones", {}).items():
            out.append({
                "key": key,
                "ts": parse_when(m["date"]),
                "date": m["date"],
                "price": m.get("price"),
                "label": m.get("label", key),
            })
        out.sort(key=lambda x: x["ts"])
        return out


def parse_when(value: str | int | float) -> int:
    """'now' | 'YYYY-MM-DD' | 'YYYY-MM-DDTHH:MM:SS' | epoch giay -> epoch giay UTC."""
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.lower() in ("now", "today"):
        return int(datetime.now(timezone.utc).timestamp())
    if text.isdigit():
        return int(text)
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"khong hieu moc thoi gian: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fmt_ts(ts: int | float | None, with_time: bool = True) -> str:
    if ts is None:
        return "-"
    dt = datetime.fromtimestamp(int(ts), timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")


def load(path: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else ROOT / "config.json"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = Config(json.load(fh))

    for env_key, dest in ENV_MAP.items():
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        try:
            _set_path(cfg, dest, _coerce(_get_path(cfg, dest), raw))
        except ValueError as exc:
            raise SystemExit(f"[config] {env_key}={raw!r} khong hop le: {exc}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return cfg
