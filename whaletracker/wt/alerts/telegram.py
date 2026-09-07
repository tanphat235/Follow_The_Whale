"""Gui canh bao qua Telegram Bot - nhanh hon email vai giay, hop copy-trade.

Cach lay thong tin (mien phi, 2 phut):
  1. Chat voi @BotFather -> /newbot -> nhan bot_token
  2. Chat 1 cau bat ky voi bot vua tao
  3. Mo https://api.telegram.org/bot<TOKEN>/getUpdates -> lay chat_id trong ket qua
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/sendMessage"
LIMIT = 4096  # gioi han do dai 1 tin nhan Telegram


def send(cfg: dict, text: str) -> None:
    token = cfg.get("bot_token") or ""
    chat_id = cfg.get("chat_id") or ""
    if not (token and chat_id):
        raise RuntimeError("thieu bot_token / chat_id trong cau hinh telegram")

    body = text if len(text) <= LIMIT else text[:LIMIT - 20] + "\n… (da cat bot)"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": body,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()

    req = urllib.request.Request(API.format(token=token), data=payload)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram tu choi: {data.get('description')}")
