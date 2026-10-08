"""Extraction validation, research with the offline model, the scoring rubric, the bounded judgment, contacts."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence

import pytest

from scout.icp import Config
from scout.llm.base import Completion, Message
from scout.llm.factory import build_chat_model
from scout.models import CompanyProfile
from scout.qualify.contacts import ClientContact, select_contact
from scout.qualify.judge import apply_judgment
from scout.qualify.rubric import score_rules
from scout.research.agent import research_account
from scout.research.crawler import Crawler
from scout.research.extract import validate_extraction
from scout.research.html_text import Block, ParsedPage
from scout.synthetic.spec import CompanySpec
from tests.conftest import TODAY


class Scripted:
    """A model that returns canned replies in order (and records the prompts)."""

    def __init__(self, *replies: dict[str, object]) -> None:
        self.replies = [json.dumps(r) for r in replies]
        self.prompts: list[Sequence[Message]] = []

    @property
    def label(self) -> str:
        return "scripted"

    @property
    def is_local(self) -> bool:
        return True

    def complete(self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0) -> Completion:
        self.prompts.append(messages)
        return Completion(text=self.replies.pop(0), model="scripted")


def _page(url: str, *blocks: tuple[str, dt.date | None], page_date: dt.date | None = None) -> ParsedPage:
    return ParsedPage(url=url, blocks=[Block(text=t, date=d) for t, d in blocks], page_date=page_date)


def test_validation_keeps_only_grounded_items(config: Config) -> None:
    pages = {
        "P1": _page(
            "https://a.example/",
            ("We operate a fleet of 120 vans. About 300 people work here.", None),
            page_date=dt.date(2026, 9, 1),
        ),
        "P2": _page(
            "https://a.example/careers.html",
            ("Fleet Manager Leeds · Posted 1 September 2026", dt.date(2026, 9, 1)),
            ("Dispatcher Leeds · Posted 3 January 2025", dt.date(2025, 1, 3)),
        ),
        "P3": _page(
            "https://a.example/team.html",
            ("Jane Roe Head of Fleet jane.roe@a.example", None),
            ("John Doe CEO Contact via our main office", None),
        ),
    }
    data = {
        "company_name": "A Ltd",
        "facts": [
            {"field": "fleet_size", "value": "120", "page": "P1", "quote": "We operate a fleet of 120 vans."},
            {"field": "employees", "value": "450", "page": "P1", "quote": "About 300 people work here."},
            {"field": "segment", "value": "spaceflight", "page": "P1", "quote": "We operate a fleet of 120 vans."},
            {"field": "founded", "value": "1990", "page": "P1", "quote": "Founded in 1990 by two brothers."},
            {"field": "country", "value": "gb", "page": "P2", "quote": "We operate a fleet of 120 vans"},
        ],
        "signals": [
            {"type": "hiring", "detail": "Fleet Manager", "page": "P2", "quote": "Fleet Manager"},
            {"type": "hiring", "detail": "Dispatcher", "page": "P2", "quote": "Dispatcher Leeds"},
            {"type": "funding", "detail": "", "page": "P1", "quote": "raised $5 million"},
        ],
        "people": [
            {
                "name": "Jane Roe",
                "title": "Head of Fleet",
                "email": "jane.roe@a.example",
                "page": "P3",
                "quote": "Jane Roe Head of Fleet",
            },
            {"name": "John Doe", "title": "CEO", "email": "john.doe@a.example", "page": "P3", "quote": "John Doe CEO"},
        ],
    }
    out = validate_extraction(data, pages, config, today=TODAY)
    fields = {f.field: f for f in out.facts}
    assert fields["fleet_size"].number == 120
    assert "employees" not in fields  # 450 is not in its quote
    assert "segment" not in fields  # unknown segment
    assert "founded" not in fields  # quote not on any page
    assert fields["country"].value == "GB" and fields["country"].citation.url == "https://a.example/"  # re-attributed
    reasons = {r.reason for r in out.rejected}
    assert {
        "number not in the quote",
        "unknown segment",
        "quote not on any crawled page",
        "email not printed on the page",
    } <= reasons
    hiring = {s.detail: s for s in out.signals if s.type == "hiring"}
    assert hiring["Fleet Manager"].current and hiring["Fleet Manager"].date_source == "block"
    assert not hiring["Dispatcher"].current and hiring["Dispatcher"].age_days == (TODAY - dt.date(2025, 1, 3)).days
    assert not any(s.type == "funding" for s in out.signals)
    people = {p.name: p for p in out.people}
    assert people["Jane Roe"].email == "jane.roe@a.example"
    assert people["John Doe"].email is None  # never guessed
    assert validate_extraction(data, pages, config, today=TODAY, keep_people=False).people == []


def _research(crawler: Crawler, config: Config, domain: str, **kw: object) -> CompanyProfile:
    model = build_chat_model(__import__("scout.config", fromlist=["Settings"]).Settings(llm_provider="fake"), **kw)  # type: ignore[arg-type]
    return research_account(f"https://{domain}/", crawler=crawler, model=model, config=config, today=TODAY).profile


def test_offline_research_finds_the_planted_signals(crawler: Crawler, config: Config, specs: list[CompanySpec]) -> None:
    for spec in specs[:25]:
        profile = _research(crawler, config, spec.domain)
        assert {s.type for s in profile.signals} == {s.type for s in spec.signals}, spec.domain
        for gold in spec.signals:
            got = next(s for s in profile.signals if s.type == gold.type)
            assert got.current == gold.current, (spec.domain, gold.type)


def test_injection_is_quarantined_and_recorded(crawler: Crawler, config: Config) -> None:
    profile = _research(crawler, config, "sunridge-solar.example")
    assert len(profile.injection_findings) == 1
    assert all("10/10" not in f.citation.quote for f in profile.facts)


@pytest.mark.parametrize(
    ("domain", "route", "reason"),
    [
        ("trackright.example", "disqualified", "a competitor"),
        ("northbeam-logistics.example", "disqualified", "already a Wayline customer"),
        ("southerncross-haulage.example", "disqualified", "outside the sales regions"),
        ("tworivers-towing.example", "disqualified", "below the floor"),
        ("fleetforge-games.example", "disqualified", "is excluded"),
        ("duneview-pools.example", "nurture", None),
        ("brightwater-fs.example", "qualified", None),
        ("sterling-septic.example", "nurture", None),
        ("redwood-facility.example", "nurture", None),
    ],
)
def test_rubric_routes(crawler: Crawler, config: Config, domain: str, route: str, reason: str | None) -> None:
    q = score_rules(_research(crawler, config, domain), config)
    assert q.route == route
    if reason:
        assert any(reason in d for d in q.disqualifiers)
    for line in q.lines:
        assert line.reason
        if line.points:
            assert line.evidence, line.criterion


def test_stale_signal_scores_zero_with_its_age(crawler: Crawler, config: Config) -> None:
    q = score_rules(_research(crawler, config, "crescentcity-plumbing.example"), config)
    hiring = next(line for line in q.lines if line.criterion == "Signal: Hiring")
    assert hiring.points == 0 and "window is 180 days" in hiring.reason


def test_signals_are_capped(config: Config, crawler: Crawler) -> None:
    profile = _research(crawler, config, "tallgrass-couriers.example")
    q = score_rules(profile, config)
    assert sum(line.points for line in q.lines if line.criterion.startswith("Signal")) <= config.rubric.signals_cap


def test_judgment_is_bounded_and_needs_evidence(crawler: Crawler, config: Config) -> None:
    profile = _research(crawler, config, "bramble-landscaping.example")
    base = score_rules(profile, config)
    assert base.route == "nurture"
    sid = profile.signals[0].id
    q, calls = apply_judgment(profile, base, config, Scripted({"adjustment": 50, "reason": "big", "evidence": [sid]}))
    assert calls == 1 and q.llm_adjustment == config.rubric.llm_adjustment_max and q.route == "qualified"
    q2, _ = apply_judgment(profile, base, config, Scripted({"adjustment": 8, "reason": "trust me", "evidence": ["X9"]}))
    assert q2.llm_adjustment == 0 and "dropped" in q2.llm_reason


def test_judgment_never_touches_a_disqualified_account(crawler: Crawler, config: Config) -> None:
    profile = _research(crawler, config, "trackright.example")
    q, calls = apply_judgment(
        profile, score_rules(profile, config), config, Scripted({"adjustment": 10, "reason": "x", "evidence": ["F1"]})
    )
    assert calls == 0 and q.route == "disqualified"


def test_gullible_judge_is_fooled_only_without_the_guard(crawler: Crawler, config: Config) -> None:
    from scout.config import Settings

    gullible = build_chat_model(Settings(llm_provider="fake"), gullible=True)
    for guard, expected in ((True, "nurture"), (False, "nurture")):
        res = research_account(
            "https://sunridge-solar.example/", crawler=crawler, model=gullible, config=config, today=TODAY, guard=guard
        )
        q, _ = apply_judgment(res.profile, score_rules(res.profile, config), config, gullible)
        assert q.route == expected  # the rules-first pipeline only sees validated facts, never the raw page


def test_contact_selection(crawler: Crawler, config: Config) -> None:
    profile = _research(crawler, config, "ridgeway-utility.example")
    sel = select_contact(profile, config)
    assert sel.contact is not None and sel.contact.persona == "fleet_leader" and sel.contact.email
    again = select_contact(profile, config, suppressed={sel.contact.email})
    assert again.contact is not None and again.contact.email != sel.contact.email
    assert any("suppression" in n for n in again.notes)
    blocked = select_contact(_research(crawler, config, "pinecrest-waste.example"), config)
    assert blocked.contact is None and any("robots.txt" in n for n in blocked.notes)


def test_live_mode_contacts_come_only_from_the_client_list(crawler: Crawler, config: Config) -> None:
    profile = _research(crawler, config, "ridgeway-utility.example").model_copy(update={"mode": "live"})
    assert select_contact(profile, config).contact is None
    client = [ClientContact("ridgeway-utility.example", "Pat Lee", "Director of Operations", "pat.lee@ridgeway-utility.example"),
              ClientContact("ridgeway-utility.example", "Sam Fox", "HR Director", "sam.fox@ridgeway-utility.example")]  # fmt: skip
    chosen = select_contact(profile, config, client_contacts=client).contact
    assert chosen is not None and chosen.email == "pat.lee@ridgeway-utility.example" and chosen.source == "client_list"


def test_country_falls_back_to_the_postal_address(config: Config) -> None:
    pages = {
        "P1": _page("https://a.example/", ("We run 40 vans.", None), ("A Ltd · 12 Mill Road, Lima, Peru", None)),
        "P2": _page(
            "https://a.example/news.html",
            ("A Ltd · 12 Mill Road, Lima, Peru", None),
            ("Our customers include firms in France", None),
        ),
    }
    out = validate_extraction(
        {"facts": [{"field": "fleet_size", "value": "40", "page": "P1", "quote": "We run 40 vans."}]},
        pages,
        config,
        today=TODAY,
    )
    country = next(f for f in out.facts if f.field == "country")
    assert country.value == "PE" and country.citation.quote.endswith("Peru") and country.id == "F2"
    with_country = {"facts": [{"field": "country", "value": "pe", "page": "P1", "quote": "Lima, Peru"}]}
    assert [
        f.value for f in validate_extraction(with_country, pages, config, today=TODAY).facts if f.field == "country"
    ] == ["PE"]
