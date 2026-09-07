"""HTTP client dung chung: token-bucket throttle + retry backoff + User-Agent trinh duyet.

Vi sao can User-Agent trinh duyet: test thuc te cho thay urllib UA mac dinh bi Cloudflare
tra 403 tren gan nhu moi RPC/explorer cong cong. Doi UA la het.

Throttle theo TUNG HOST (khong phai toan cuc) vi Blockscout va Binance co han muc rieng.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import requests

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Ma loi dang thu lai. 429 = qua nhieu request, 5xx = server troc trac.
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504, 521, 522, 524}


class RateLimitError(RuntimeError):
    pass


class _Bucket:
    """Token bucket don gian, thread-safe. rate = so request cho phep moi giay."""

    def __init__(self, rate: float):
        self.rate = max(rate, 0.1)
        self.capacity = max(rate, 1.0)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def take(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) / self.rate
            time.sleep(min(wait, 1.0))


class Client:
    def __init__(self, cfg: dict | None = None, verbose: bool = True):
        http_cfg = (cfg or {}).get("http", {}) if cfg else {}
        self.rate = float(http_cfg.get("rate_limit_per_sec", 5.0))
        self.max_retries = int(http_cfg.get("max_retries", 5))
        self.timeout = int(http_cfg.get("timeout_sec", 45))
        self.ua = http_cfg.get("user_agent") or _DEFAULT_UA
        self.verbose = verbose

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.ua,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()
        self.n_requests = 0
        self.throttled: dict[str, float] = {}

    def _bucket(self, url: str) -> _Bucket:
        host = urlsplit(url).netloc
        with self._lock:
            if host not in self._buckets:
                self._buckets[host] = _Bucket(self.rate)
            return self._buckets[host]

    def get_json(self, url: str, params: dict | None = None, *,
                 rate: float | None = None, allow_status: set[int] | None = None) -> Any:
        """GET -> JSON, tu dong retry voi exponential backoff + jitter.

        rate: ghi de toc do cho host nay (Binance chiu duoc nhanh hon Blockscout).
        allow_status: cac ma loi coi la ket qua hop le, tra ve None thay vi nem loi.
        """
        bucket = self._bucket(url)
        if rate is not None and abs(bucket.rate - rate) > 1e-9:
            bucket.rate = max(rate, 0.1)
            bucket.capacity = max(rate, 1.0)

        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            bucket.take()
            try:
                self.n_requests += 1
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_err = exc
                self._sleep(attempt, f"loi mang: {type(exc).__name__}")
                continue

            if allow_status and resp.status_code in allow_status:
                return None

            if resp.status_code in RETRY_STATUS:
                last_err = RateLimitError(f"HTTP {resp.status_code} tu {urlsplit(url).netloc}")
                hint = resp.headers.get("Retry-After")
                delay = float(hint) if hint and hint.isdigit() else None
                if resp.status_code == 429:
                    # Bi chan vi qua nhanh -> HA HAN TOC cua host nay lai mot nua va giu
                    # muc do den het phien. Chi thu lai voi cung toc do cu se bi chan tiep:
                    # phai tu chinh xuong dung nguong that su cua server.
                    self._throttle(bucket, urlsplit(url).netloc)
                    if delay is None:
                        delay = min(20.0 * (attempt + 1), 90.0)   # 429 can nghi lau hon 5xx
                self._sleep(attempt, f"HTTP {resp.status_code}", forced=delay)
                continue

            if resp.status_code == 451:
                raise RateLimitError(
                    f"HTTP 451 tu {urlsplit(url).netloc} - IP bi chan theo vung dia ly. "
                    "Neu dang chay tren Oracle Cloud US, hay doi sang region Singapore/Tokyo/Frankfurt."
                )

            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status_code} {url} :: {resp.text[:200]}")

            try:
                return resp.json()
            except ValueError as exc:
                last_err = exc
                self._sleep(attempt, "phan hoi khong phai JSON")
                continue

        raise RuntimeError(f"That bai sau {self.max_retries} lan thu: {url} :: {last_err}")

    def _throttle(self, bucket: "_Bucket", host: str) -> None:
        """Ha han toc cua 1 host xuong mot nua, san khong duoi 0.25 req/s."""
        with self._lock:
            if bucket.rate <= 0.26:
                return
            bucket.rate = max(0.25, bucket.rate / 2)
            bucket.capacity = max(1.0, bucket.rate)
            self.throttled[host] = bucket.rate
        if self.verbose:
            print(f"    [throttle] {host} bi 429 -> ha xuong {bucket.rate:.2f} req/s", flush=True)

    def _sleep(self, attempt: int, why: str, forced: float | None = None) -> None:
        delay = forced if forced is not None else min(2 ** attempt + random.random(), 30.0)
        if self.verbose:
            print(f"    [retry {attempt + 1}] {why}, cho {delay:.1f}s", flush=True)
        time.sleep(delay)
