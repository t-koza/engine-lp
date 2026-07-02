"""共通ユーティリティ: HTTPセッション、レート制限、robots.txt、ログ。

設計方針(正確性最優先):
- 推測でデータを作らない。取得できなかった値は空文字のまま返す。
- 全URLは実際にGETして最終ステータス(リダイレクト後)を記録する。
- robots.txt を尊重し、ホスト毎に最小間隔をあけて低レートで巡回する。
- プロキシ環境では REQUESTS_CA_BUNDLE / HTTPS_PROXY を環境変数経由で自動利用。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

USER_AGENT = os.environ.get(
    "SCRAPER_UA",
    "Mozilla/5.0 (compatible; rental-store-list-bot/1.0; +contact form sales list research)",
)

# ホスト毎の最小リクエスト間隔(秒)。礼儀正しく低レートに。
DEFAULT_MIN_INTERVAL = float(os.environ.get("SCRAPER_MIN_INTERVAL", "1.5"))
REQUEST_TIMEOUT = float(os.environ.get("SCRAPER_TIMEOUT", "25"))
MAX_RETRIES = int(os.environ.get("SCRAPER_RETRIES", "3"))

log = logging.getLogger("scraper")


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    status: int = 0          # 0 = 接続失敗/例外
    ok: bool = False         # True なら最終的に HTTP 200
    redirected: bool = False
    cross_host_redirect: bool = False
    content_type: str = ""
    text: str = ""
    bytes_len: int = 0
    error: str = ""
    elapsed_ms: int = 0


@dataclass
class HostThrottle:
    min_interval: float = DEFAULT_MIN_INTERVAL
    _last: Dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            delta = now - last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last[host] = time.monotonic()


class Fetcher:
    """robots尊重・レート制限付きのHTTP取得クライアント。"""

    def __init__(self, respect_robots: bool = True, min_interval: float = DEFAULT_MIN_INTERVAL):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        if ca and os.path.exists(ca):
            self.session.verify = ca
        self.respect_robots = respect_robots
        self.throttle = HostThrottle(min_interval=min_interval)
        self._robots: Dict[str, Optional[RobotFileParser]] = {}
        self._robots_lock = threading.Lock()

    # -- robots ---------------------------------------------------------
    def _robots_for(self, url: str) -> Optional[RobotFileParser]:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        with self._robots_lock:
            if base in self._robots:
                return self._robots[base]
        rp = RobotFileParser()
        rp.set_url(base + "/robots.txt")
        try:
            self.throttle.wait(parsed.netloc)
            resp = self.session.get(base + "/robots.txt", timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp = None  # robots無し→許可とみなす
        except Exception as e:  # noqa: BLE001
            log.warning("robots取得失敗 %s: %s", base, e)
            rp = None
        with self._robots_lock:
            self._robots[base] = rp
        return rp

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rp = self._robots_for(url)
        if rp is None:
            return True
        try:
            return rp.can_fetch(USER_AGENT, url)
        except Exception:  # noqa: BLE001
            return True

    # -- fetch ----------------------------------------------------------
    def fetch(self, url: str, method: str = "GET") -> FetchResult:
        res = FetchResult(url=url)
        if not self.allowed(url):
            res.error = "robots.txt により不許可"
            return res
        host = urlparse(url).netloc
        last_err = ""
        for attempt in range(1, MAX_RETRIES + 1):
            self.throttle.wait(host)
            t0 = time.monotonic()
            try:
                resp = self.session.request(
                    method, url, timeout=REQUEST_TIMEOUT, allow_redirects=True
                )
                res.elapsed_ms = int((time.monotonic() - t0) * 1000)
                res.status = resp.status_code
                res.final_url = resp.url
                res.content_type = resp.headers.get("Content-Type", "")
                res.redirected = len(resp.history) > 0
                res.cross_host_redirect = (
                    urlparse(resp.url).netloc != host if res.redirected else False
                )
                res.ok = resp.status_code == 200
                if method == "GET" and "html" in res.content_type.lower():
                    resp.encoding = resp.apparent_encoding or resp.encoding
                    res.text = resp.text
                    res.bytes_len = len(resp.content)
                return res
            except requests.RequestException as e:
                last_err = f"{type(e).__name__}: {e}"
                wait = 2 ** attempt
                log.warning("取得失敗(%d/%d) %s: %s — %ds待機", attempt, MAX_RETRIES, url, last_err, wait)
                time.sleep(wait)
        res.error = last_err or "不明なエラー"
        return res


def setup_logging(logfile: Optional[str] = None, level: int = logging.INFO) -> None:
    handlers = [logging.StreamHandler()]
    if logfile:
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )
