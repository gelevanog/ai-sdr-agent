"""The polite crawler: company pages only, robots.txt honoured, one request per host every N seconds.

Three fetchers share it:
  * FileFetcher reads the generated synthetic web from disk (tests, evaluation);
  * HttpFetcher(base_url=...) fetches the synthetic web from the local static server (Docker demo);
  * HttpFetcher() fetches real public company pages (live mode, for a client's own target list).

Live mode is deliberately narrow: it starts at the homepage, follows only same-site links whose path looks like a
company page (about, team, careers, news, press, pricing...), stops at `max_pages`, never submits forms, never
fetches a disallowed path, and keeps raw page text only for the retention period.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from scout.logging_config import get_logger
from scout.research.html_text import ParsedPage, parse_html, same_site

log = get_logger(__name__)

PRIORITY_KEYWORDS = (
    "about",
    "company",
    "team",
    "leadership",
    "people",
    "careers",
    "jobs",
    "news",
    "press",
    "media",
    "blog",
    "services",
    "products",
    "solutions",
    "pricing",
    "locations",
)
_SKIP_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip", ".mp4", ".webp", ".ico", ".css", ".js")


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content_type: str = "text/html"
    text: str = ""


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


def _path_of(url: str) -> str:
    path = urlsplit(url).path or "/"
    return "/index.html" if path == "/" else path


class FileFetcher:
    """Serves https://<domain>/<path> from <root>/<domain>/<path> (the generated synthetic web)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.requests: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.requests.append(url)
        parts = urlsplit(url)
        host = parts.hostname or ""
        path = self.root / host / _path_of(url).lstrip("/")
        try:
            resolved = path.resolve()
            resolved.relative_to(self.root.resolve())
        except (ValueError, OSError):
            return FetchResult(url, 404)
        if not resolved.is_file():
            return FetchResult(url, 404)
        content_type = "text/plain" if resolved.suffix == ".txt" else "text/html"
        return FetchResult(url, 200, content_type, resolved.read_text(encoding="utf-8"))


class HttpFetcher:
    """Real HTTP. With `base_url`, https://<domain>/<path> is fetched from <base_url>/<domain>/<path> (the synthetic
    web's static server); without it, the URL itself is fetched (live mode)."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 15.0,
        base_url: str | None = None,
        max_bytes: int = 2_000_000,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.max_bytes = max_bytes
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "text/html,text/plain;q=0.9"},
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    def _target(self, url: str) -> str:
        if self.base_url is None:
            return url
        parts = urlsplit(url)
        return f"{self.base_url}/{parts.hostname}{_path_of(url)}"

    def fetch(self, url: str) -> FetchResult:
        target = self._target(url)
        for _ in range(3):  # follow at most two same-site redirects
            try:
                response = self._client.get(target)
            except httpx.HTTPError as exc:
                log.warning("crawl.fetch_error", url=url, error=type(exc).__name__)
                return FetchResult(url, 599)
            if response.status_code in {301, 302, 303, 307, 308} and "location" in response.headers:
                nxt = str(response.url.join(response.headers["location"]))
                if self.base_url is None and not same_site(url, nxt):
                    return FetchResult(url, 310)  # off-site redirect: not followed
                target = nxt
                continue
            content_type = response.headers.get("content-type", "").split(";")[0].strip() or "text/html"
            body = response.content[: self.max_bytes].decode(response.encoding or "utf-8", errors="replace")
            return FetchResult(url, response.status_code, content_type, body)
        return FetchResult(url, 310)

    def close(self) -> None:
        self._client.close()


class RateLimiter:
    """At most one request per `min_interval` seconds per host."""

    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()
        self.waited = 0.0

    def wait(self, host: str) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = self._clock()
            start = max(now, self._next.get(host, 0.0))
            self._next[host] = start + self.min_interval
        if start > now:
            self.waited += start - now
            self._sleep(start - now)


class RobotsPolicy:
    """robots.txt per host, cached. Follows RFC 9309: a missing file (4xx) allows everything; a server error or an
    unreachable host means "assume disallowed"."""

    def __init__(self, fetcher: Fetcher, user_agent: str, limiter: RateLimiter | None = None) -> None:
        self.fetcher = fetcher
        self.user_agent = user_agent
        self.limiter = limiter
        self._cache: dict[str, RobotFileParser | bool] = {}

    def _robots_for(self, url: str) -> RobotFileParser | bool:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if host in self._cache:
            return self._cache[host]
        robots_url = urlunsplit((parts.scheme or "https", parts.netloc, "/robots.txt", "", ""))
        if self.limiter:
            self.limiter.wait(host)
        result = self.fetcher.fetch(robots_url)
        entry: RobotFileParser | bool
        if 200 <= result.status < 300:
            parser = RobotFileParser()
            parser.parse(result.text.splitlines())
            entry = parser
        elif 400 <= result.status < 500:
            entry = True
        else:
            entry = False
        self._cache[host] = entry
        return entry

    def allowed(self, url: str) -> bool:
        robots = self._robots_for(url)
        if isinstance(robots, bool):
            return robots
        return robots.can_fetch(self.user_agent, url)


@dataclass
class CrawledPage:
    url: str
    fetched_at: dt.datetime
    parsed: ParsedPage
    html: str


@dataclass
class CrawlResult:
    start_url: str
    pages: list[CrawledPage] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    """(url, reason): disallowed by robots.txt, off-site, not HTML, HTTP error, page limit."""

    @property
    def blocked_by_robots(self) -> bool:
        return not self.pages and any(reason == "robots.txt" for _, reason in self.skipped)


def canonical(url: str) -> str:
    """Drops the fragment and maps /index.html to / so a homepage is fetched once."""
    parts = urlsplit(url.split("#")[0])
    path = parts.path or "/"
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, parts.query, ""))


def _priority(url: str) -> int:
    path = urlsplit(url).path.lower()
    if path in {"", "/", "/index.html"}:
        return 0
    for rank, keyword in enumerate(PRIORITY_KEYWORDS, start=1):
        if keyword in path:
            return rank
    return 100


class Crawler:
    def __init__(
        self,
        fetcher: Fetcher,
        *,
        user_agent: str,
        max_pages: int = 12,
        limiter: RateLimiter | None = None,
        live: bool = False,
        now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        self.fetcher = fetcher
        self.user_agent = user_agent
        self.max_pages = max_pages
        self.limiter = limiter or RateLimiter(0)
        self.robots = RobotsPolicy(fetcher, user_agent, self.limiter)
        self.live = live
        self.now = now

    def _wanted(self, url: str) -> bool:
        path = urlsplit(url).path.lower()
        if path.endswith(_SKIP_SUFFIXES) or urlsplit(url).query:
            return False
        if not self.live:
            return True
        # Live mode: only pages that describe the company (no product catalogues, shops, logins, user content).
        return _priority(url) < 100

    def crawl(self, start_url: str) -> CrawlResult:
        start_url = canonical(start_url)
        result = CrawlResult(start_url=start_url)
        queue: list[str] = [start_url]
        seen: set[str] = {start_url}
        while queue and len(result.pages) < self.max_pages:
            queue.sort(key=_priority)
            url = queue.pop(0)
            if not self.robots.allowed(url):
                result.skipped.append((url, "robots.txt"))
                continue
            self.limiter.wait(urlsplit(url).hostname or "")
            fetched = self.fetcher.fetch(url)
            if fetched.status != 200:
                result.skipped.append((url, f"HTTP {fetched.status}"))
                continue
            if "html" not in fetched.content_type:
                result.skipped.append((url, f"not HTML ({fetched.content_type})"))
                continue
            parsed = parse_html(url, fetched.text)
            result.pages.append(CrawledPage(url=url, fetched_at=self.now(), parsed=parsed, html=fetched.text))
            for raw_link in parsed.links:
                link = canonical(raw_link)
                if link in seen:
                    continue
                seen.add(link)
                if not same_site(start_url, link):
                    continue
                if not self._wanted(link):
                    result.skipped.append((link, "not a company page"))
                    continue
                queue.append(link)
        for url in queue:
            result.skipped.append((url, "page limit"))
        return result
