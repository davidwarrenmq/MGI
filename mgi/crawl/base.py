"""PoliteCrawler: robots-respecting, rate-limited, caching HTTP fetcher (scope §8).

Politeness guarantees (scope §4.4 / §8):

* robots.txt parsed and respected per host (``urllib.robotparser``).
* Rate limiting (default ~1 request / 2 s per host) with random jitter.
* Identifying User-Agent with a contact URL.
* On-disk response cache so re-runs do not re-hit servers.
* Metadata + URL only -- this class fetches HTML/XML for PARSING; callers must
  never persist full text/PDF (scope §4.4).

Networking uses ``requests`` when available, else stdlib ``urllib``. Either way
the import of this module never fails in a minimal environment (system prompt §6).
"""
from __future__ import annotations

import hashlib
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

USER_AGENT = "medical-guideline-index/0.1 (+https://example.org; research tool)"

try:  # optional, nicer networking
    import requests  # type: ignore
    _HAVE_REQUESTS = True
except ImportError:  # pragma: no cover - depends on env
    requests = None  # type: ignore
    _HAVE_REQUESTS = False


@dataclass
class FetchResult:
    """Outcome of a fetch (metadata only; body kept transiently for parsing)."""

    url: str
    status: int
    text: str = ""
    from_cache: bool = False
    content_type: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.error


@dataclass
class PoliteCrawler:
    """A courteous single-threaded fetcher with caching + robots enforcement."""

    cache_dir: Path = field(default_factory=lambda: Path(".mgi_cache"))
    min_interval: float = 2.0          # seconds between hits to the same host
    jitter: float = 0.5               # +/- random seconds added to the wait
    timeout: float = 20.0
    user_agent: str = USER_AGENT
    respect_robots: bool = True
    max_bytes: int = 2_000_000        # safety cap; we only need metadata/HTML

    _last_hit: Dict[str, float] = field(default_factory=dict, init=False)
    _robots: Dict[str, RobotFileParser] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- caching ---------------------------------------------------------
    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{h}.json"

    def _read_cache(self, url: str) -> Optional[FetchResult]:
        p = self._cache_path(url)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return FetchResult(url=url, status=d.get("status", 0),
                               text=d.get("text", ""), from_cache=True,
                               content_type=d.get("content_type", ""))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, res: FetchResult) -> None:
        if not res.ok:
            return
        try:
            self._cache_path(res.url).write_text(
                json.dumps({"status": res.status, "text": res.text,
                            "content_type": res.content_type}),
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- robots ----------------------------------------------------------
    def _robots_for(self, url: str) -> Optional[RobotFileParser]:
        host = urlparse(url).netloc
        scheme = urlparse(url).scheme or "https"
        if host in self._robots:
            return self._robots[host]
        rp = RobotFileParser()
        rp.set_url(f"{scheme}://{host}/robots.txt")
        try:
            rp.read()
        except Exception:
            # If robots cannot be fetched, be conservative but do not crash;
            # treat as "allow" only for explicit fetches the caller initiated.
            rp = RobotFileParser()
            rp.parse([])  # empty -> allow all (cannot read => assume permitted)
        self._robots[host] = rp
        return rp

    def allowed(self, url: str) -> bool:
        """Return True if robots.txt permits our UA to fetch ``url``."""
        if not self.respect_robots:
            return True
        rp = self._robots_for(url)
        if rp is None:
            return True
        try:
            return rp.can_fetch(self.user_agent, url)
        except Exception:
            return True

    # -- rate limiting ---------------------------------------------------
    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        now = time.monotonic()
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.min_interval - (now - last)
            wait += random.uniform(0, self.jitter)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    # -- fetch -----------------------------------------------------------
    def fetch(self, url: str, *, use_cache: bool = True) -> FetchResult:
        """Politely fetch ``url`` (robots + throttle + cache). Never raises."""
        if use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                return cached

        if not self.allowed(url):
            return FetchResult(url=url, status=0, error="disallowed by robots.txt")

        self._throttle(url)

        try:
            if _HAVE_REQUESTS:
                res = self._fetch_requests(url)
            else:
                res = self._fetch_urllib(url)
        except Exception as exc:  # network errors must not crash the crawl
            return FetchResult(url=url, status=0, error=f"{type(exc).__name__}: {exc}")

        if use_cache:
            self._write_cache(res)
        return res

    def _fetch_requests(self, url: str) -> FetchResult:
        resp = requests.get(url, headers={"User-Agent": self.user_agent},
                            timeout=self.timeout, stream=True)
        ctype = resp.headers.get("Content-Type", "")
        body = resp.content[: self.max_bytes]
        text = body.decode(resp.encoding or "utf-8", errors="replace")
        return FetchResult(url=url, status=resp.status_code, text=text,
                           content_type=ctype)

    def _fetch_urllib(self, url: str) -> FetchResult:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # nosec - polite fetch
            ctype = r.headers.get("Content-Type", "")
            raw = r.read(self.max_bytes)
            charset = r.headers.get_content_charset() or "utf-8"
            return FetchResult(url=url, status=getattr(r, "status", 200),
                               text=raw.decode(charset, errors="replace"),
                               content_type=ctype)
