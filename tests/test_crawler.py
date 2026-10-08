"""The crawler: robots.txt, rate limits, live-mode restrictions, redirects, recorded live fixtures."""

from __future__ import annotations

from pathlib import Path

import httpx

from scout.research.crawler import Crawler, FileFetcher, HttpFetcher, RateLimiter, RobotsPolicy, canonical
from tests.conftest import FIXTURES

LIVE = FIXTURES / "live"


def live_transport(log: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        if request.url.host != "northwind-fleet.test":
            return httpx.Response(500, text="the test must not leave the fixture site")
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text=(LIVE / "robots.txt").read_text(), headers={"content-type": "text/plain"})
        if path == "/old-about":
            return httpx.Response(301, headers={"location": "https://evil.test/landing"})
        name = {"/": "index.html", "/about": "about.html", "/careers": "careers.html", "/news": "news.html"}.get(path)
        if name is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=(LIVE / name).read_text(), headers={"content-type": "text/html; charset=utf-8"})

    return httpx.MockTransport(handler)


def test_live_crawl_respects_robots_scope_and_rate_limit() -> None:
    log: list[httpx.Request] = []
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    limiter = RateLimiter(2.0, clock=lambda: clock["t"], sleep=sleep)
    fetcher = HttpFetcher(user_agent="ScoutResearchBot/0.1", transport=live_transport(log))
    crawler = Crawler(fetcher, user_agent="ScoutResearchBot", max_pages=10, limiter=limiter, live=True)
    result = crawler.crawl("https://northwind-fleet.test/")
    fetched = [p.url for p in result.pages]
    assert fetched[0] == "https://northwind-fleet.test/"
    assert "https://northwind-fleet.test/careers" in fetched and "https://northwind-fleet.test/about" in fetched
    reasons = dict(result.skipped)
    assert reasons["https://northwind-fleet.test/careers/archive"] == "robots.txt"  # agent-specific rule
    assert reasons["https://northwind-fleet.test/shop/cart"] == "not a company page"
    assert reasons["https://northwind-fleet.test/login"] == "not a company page"
    assert "https://othersite.test/partner" not in [str(r.url) for r in log]
    assert all(r.url.host == "northwind-fleet.test" for r in log)
    assert not any(str(r.url).endswith(".pdf") for r in log)
    assert all(r.headers["user-agent"].startswith("ScoutResearchBot") for r in log)
    # One request per host every 2 s: robots + pages, each waited for after the first.
    assert len(sleeps) == len(log) - 1 and all(abs(s - 2.0) < 1e-6 for s in sleeps)


def test_hidden_text_and_comments_are_kept_apart() -> None:
    log: list[httpx.Request] = []
    fetcher = HttpFetcher(user_agent="ScoutResearchBot", transport=live_transport(log))
    page = (
        Crawler(fetcher, user_agent="ScoutResearchBot", live=True)
        .crawl("https://northwind-fleet.test/about")
        .pages[0]
        .parsed
    )
    assert "10/10" not in page.text
    assert any("10/10" in h for h in page.hidden_text)
    assert any("note to crawlers" in h for h in page.hidden_text)


def test_off_site_redirect_is_not_followed() -> None:
    log: list[httpx.Request] = []
    fetcher = HttpFetcher(user_agent="ScoutResearchBot", transport=live_transport(log))
    assert fetcher.fetch("https://northwind-fleet.test/old-about").status == 310
    assert all(r.url.host == "northwind-fleet.test" for r in log)


def test_robots_status_codes_follow_rfc_9309() -> None:
    def fetcher_for(status: int) -> FileFetcher:
        class F(FileFetcher):
            def fetch(self, url: str):  # type: ignore[no-untyped-def]
                from scout.research.crawler import FetchResult

                return FetchResult(url, status, "text/plain", "")

        return F(Path())

    assert RobotsPolicy(fetcher_for(404), "Bot").allowed("https://a.test/x")
    assert not RobotsPolicy(fetcher_for(503), "Bot").allowed("https://a.test/x")


def test_synthetic_robots(crawler: Crawler) -> None:
    blocked = crawler.crawl("https://duneview-pools.example/")
    assert blocked.blocked_by_robots and not blocked.pages
    partial = crawler.crawl("https://pinecrest-waste.example/")
    assert ("https://pinecrest-waste.example/team.html", "robots.txt") in partial.skipped
    assert partial.pages


def test_homepage_is_fetched_once(crawler: Crawler) -> None:
    result = crawler.crawl("https://brightwater-fs.example/index.html")
    urls = [p.url for p in result.pages]
    assert urls.count("https://brightwater-fs.example/") == 1
    assert len(urls) == len(set(urls))


def test_file_fetcher_cannot_escape_its_root(web_dir: Path) -> None:
    fetcher = FileFetcher(web_dir)
    assert fetcher.fetch("https://brightwater-fs.example/../../etc/passwd").status == 404


def test_canonical() -> None:
    assert canonical("https://A.test/index.html#top") == "https://a.test/"
    assert canonical("https://a.test/news/index.html") == "https://a.test/news/"
