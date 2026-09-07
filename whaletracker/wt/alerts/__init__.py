"""Kenh gui canh bao. Bat/tat doc lap trong config.json hoac bang bien moi truong."""
from __future__ import annotations


def send_all(cfg, subject: str, text: str, html_body: str | None = None) -> dict:
    """Gui qua tat ca kenh dang bat. Mot kenh loi khong lam chet kenh con lai."""
    results = {}
    email_cfg = cfg.get("alerts", {}).get("email", {})
    tg_cfg = cfg.get("alerts", {}).get("telegram", {})

    if email_cfg.get("enabled"):
        from .email import send as send_email
        try:
            send_email(email_cfg, subject, text, html_body)
            results["email"] = "ok"
        except Exception as exc:
            results["email"] = f"loi: {type(exc).__name__}: {exc}"
    if tg_cfg.get("enabled"):
        from .telegram import send as send_tg
        try:
            send_tg(tg_cfg, text)
            results["telegram"] = "ok"
        except Exception as exc:
            results["telegram"] = f"loi: {type(exc).__name__}: {exc}"

    if not results:
        results["none"] = "chua bat kenh canh bao nao trong config"
    return results
