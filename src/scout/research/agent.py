"""The research agent for one account: crawl, guard, extract (one model call), validate, date the signals."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Any

from scout.icp import Config
from scout.llm.base import ChatModel, LLMError, Message
from scout.llm.jsonparse import JSONReplyError, parse_json_object
from scout.logging_config import get_logger
from scout.models import CompanyProfile, PageRef
from scout.research.crawler import Crawler, CrawlResult
from scout.research.extract import build_extract_messages, validate_extraction
from scout.research.html_text import Block, ParsedPage
from scout.research.injection import InjectionFinding, guard_page

log = get_logger(__name__)


@dataclass
class ResearchResult:
    profile: CompanyProfile
    crawl: CrawlResult
    guarded_pages: dict[str, ParsedPage] = field(default_factory=dict)
    """Page id -> the page text the model saw."""
    error: str | None = None
    raw: dict[str, Any] | None = None
    """The model's reply before validation (the evaluation measures citation validity on it)."""


def prepare_pages(crawl: CrawlResult, *, guard: bool) -> tuple[dict[str, ParsedPage], list[InjectionFinding]]:
    """Page ids P1..Pn and the text each page contributes. With the guard off, hidden text is appended as if a
    naive scraper had read it (that is the ablation), and nothing is quarantined."""
    pages: dict[str, ParsedPage] = {}
    findings: list[InjectionFinding] = []
    for i, crawled in enumerate(crawl.pages, start=1):
        parsed = crawled.parsed
        if guard:
            parsed, found = guard_page(parsed)
            findings.extend(found)
        elif parsed.hidden_text:
            extra = [Block(text=text) for text in parsed.hidden_text]
            parsed = parsed.model_copy(update={"blocks": [*parsed.blocks, *extra], "hidden_text": []})
        pages[f"P{i}"] = parsed
    return pages, findings


def complete_json(
    model: ChatModel, messages: list[Message], *, max_tokens: int, retries: int = 1
) -> tuple[dict[str, Any], int]:
    """One model call, plus one repair request if the reply is not a JSON object. Returns (data, calls)."""
    calls = 0
    convo = list(messages)
    last_error = ""
    for _ in range(retries + 1):
        calls += 1
        completion = model.complete(convo, max_tokens=max_tokens)
        try:
            return parse_json_object(completion.text), calls
        except JSONReplyError as exc:
            last_error = str(exc)
            convo = [
                *messages,
                {"role": "assistant", "content": completion.text[:4000]},
                {
                    "role": "user",
                    "content": "That was not a single valid JSON object. Reply again with the JSON object only.",
                },
            ]
    raise LLMError(f"no valid JSON after {calls} attempts: {last_error}")


def research_account(
    url: str,
    *,
    crawler: Crawler,
    model: ChatModel,
    config: Config,
    today: dt.date,
    guard: bool = True,
    live: bool = False,
    max_tokens: int = 16000,
) -> ResearchResult:
    started = time.monotonic()
    crawl = crawler.crawl(url)
    domain = (crawl.start_url.split("//", 1)[-1]).split("/", 1)[0]
    profile = CompanyProfile(
        domain=domain,
        url=crawl.start_url,
        name=domain,
        skipped=crawl.skipped,
        blocked_by_robots=crawl.blocked_by_robots,
        mode="live" if live else "synthetic",
        model=model.label,
        researched_at=dt.datetime.now(dt.UTC),
        pages=[
            PageRef(url=p.url, title=p.parsed.title, page_date=p.parsed.page_date, fetched_at=p.fetched_at)
            for p in crawl.pages
        ],
    )
    if not crawl.pages:
        profile.seconds = round(time.monotonic() - started, 2)
        return ResearchResult(profile=profile, crawl=crawl, error="no pages could be fetched")
    pages, findings = prepare_pages(crawl, guard=guard)
    profile.injection_findings = findings
    messages = build_extract_messages(crawl.start_url, list(pages.items()), config, spotlighting=guard)
    try:
        data, calls = complete_json(model, messages, max_tokens=max_tokens)  # type: ignore[arg-type]
    except LLMError as exc:
        log.warning("research.model_error", domain=domain, error=str(exc)[:200])
        profile.seconds = round(time.monotonic() - started, 2)
        return ResearchResult(profile=profile, crawl=crawl, guarded_pages=pages, error=str(exc))
    extracted = validate_extraction(data, pages, config, today=today, keep_people=not live)
    profile.llm_calls = calls
    profile.name = extracted.name or domain
    profile.segment = extracted.segment
    profile.industry = extracted.industry
    profile.facts = extracted.facts
    profile.signals = extracted.signals
    profile.people = extracted.people
    profile.rejected = extracted.rejected
    profile.seconds = round(time.monotonic() - started, 2)
    log.info(
        "research.done",
        domain=domain,
        pages=len(crawl.pages),
        facts=len(profile.facts),
        signals=len(profile.signals),
        rejected=len(profile.rejected),
        injections=len(findings),
        seconds=profile.seconds,
    )
    return ResearchResult(profile=profile, crawl=crawl, guarded_pages=pages, raw=data)
