"""Gui canh bao qua email SMTP.

Voi Gmail: bat 2FA roi tao "App Password" (16 ky tu) tai
https://myaccount.google.com/apppasswords - KHONG dung mat khau dang nhap thuong.
Dat vao bien moi truong WT_EMAIL_APP_PASSWORD, dung ghi vao config.json.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage


def send(cfg: dict, subject: str, text: str, html_body: str | None = None) -> None:
    user = cfg.get("username") or ""
    password = cfg.get("app_password") or ""
    to = cfg.get("to") or user
    if not (user and password and to):
        raise RuntimeError("thieu username / app_password / to trong cau hinh email")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    host = cfg.get("smtp_host", "smtp.gmail.com")
    port = int(cfg.get("smtp_port", 587))
    ctx = ssl.create_default_context()

    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as srv:
            srv.login(user, password)
            srv.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as srv:
            srv.ehlo()
            srv.starttls(context=ctx)
            srv.login(user, password)
            srv.send_message(msg)
