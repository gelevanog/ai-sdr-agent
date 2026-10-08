"""The claim checker, the draft loop, spam checks, scheduling and meeting slots."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from scout.config import Settings
from scout.icp import Config
from scout.llm.factory import build_chat_model
from scout.models import Claim, CompanyProfile, Contact, DraftVariant, Email
from scout.outreach.checker import build_evidence, check_variants, deterministic_check, trim_unsupported
from scout.outreach.draft import draft_account, normalize_evidence, offer_id_map
from scout.outreach.schedule import add_business_days, next_business_slot, plan_sequence
from scout.outreach.spam import check_email, flesch_reading_ease
from scout.qualify.rubric import score_rules
from scout.replies.meetings import find_slot
from scout.research.agent import research_account
from scout.research.crawler import Crawler
from tests.conftest import TODAY
from tests.test_research_qualify import Scripted

CONTACT = Contact(
    name="Ruth Delgado",
    title="Fleet Manager",
    email="ruth@brightwater-fs.example",
    persona="fleet_leader",
    source="team_page",
)


@pytest.fixture
def profile(crawler: Crawler, config: Config) -> CompanyProfile:
    fake = build_chat_model(Settings(llm_provider="fake"))
    return research_account(
        "https://brightwater-fs.example/", crawler=crawler, model=fake, config=config, today=TODAY
    ).profile


def _variant(body: str, *claims: Claim, subject: str = "Fleet safety") -> DraftVariant:
    return DraftVariant(variant="A", angle="", emails=[Email(step=1, subject=subject, body=body, claims=list(claims))])


def _ids(profile: CompanyProfile) -> dict[str, str]:
    ids = {f.field: f.id for f in profile.facts}
    ids.update({s.type: s.id for s in profile.signals})
    return ids


def test_supported_claims_pass(profile: CompanyProfile, config: Config) -> None:
    ids = _ids(profile)
    ev = build_evidence(profile, CONTACT, config)
    v = _variant(
        "Hi Ruth,\n\nI noticed Brightwater is hiring a Fleet Safety Manager. With 310 service vans, small gains add up. "
        "Would a 20-minute call to compare notes on your fleet be useful?\n\nDana",
        Claim(text="I noticed Brightwater is hiring a Fleet Safety Manager.", evidence=[ids["hiring"]]),
        Claim(text="With 310 service vans", evidence=[ids["fleet_size"]]),
    )
    issues = deterministic_check(v, ev, config)
    assert [i for i in issues if i.severity == "block"] == []


@pytest.mark.parametrize(
    ("sentence", "evidence_key", "kind"),
    [
        ("With 350 service vans, small gains add up.", "fleet_size", "unknown_number"),
        ("I saw you just opened a depot in Indianapolis.", "hiring", "unknown_name"),
        ("I noticed your Fleet Safety Manager role went up in March.", "hiring", "unknown_date"),
        ("Your fleet runs 24 hours a day.", None, "unsupported_claim"),
    ],
)
def test_unsupported_details_are_blocked(
    profile: CompanyProfile, config: Config, sentence: str, evidence_key: str | None, kind: str
) -> None:
    ids = _ids(profile)
    ev = build_evidence(profile, CONTACT, config)
    claim = Claim(text=sentence, evidence=[ids[evidence_key]] if evidence_key else [])
    issues = deterministic_check(_variant(f"Hi Ruth,\n\n{sentence}\n\nDana", claim), ev, config)
    assert any(i.kind == kind and i.severity == "block" for i in issues), [i.message for i in issues]


def test_stale_signal_cannot_be_called_recent(crawler: Crawler, config: Config) -> None:
    fake = build_chat_model(Settings(llm_provider="fake"))
    stale = research_account(
        "https://sterling-septic.example/", crawler=crawler, model=fake, config=config, today=TODAY
    ).profile
    sid = next(s.id for s in stale.signals if s.type == "hiring")
    ev = build_evidence(stale, CONTACT, config)
    bad = _variant(
        "Hi Ruth,\n\nI saw you're currently hiring a Fleet Manager.\n\nDana",
        Claim(text="I saw you're currently hiring a Fleet Manager.", evidence=[sid]),
    )
    assert any("old signal" in i.message for i in deterministic_check(bad, ev, config))


def test_proof_point_claims_are_tied_to_the_offer(profile: CompanyProfile, config: Config) -> None:
    ev = build_evidence(profile, CONTACT, config)
    text = "Northbeam Logistics reduced idle time by 23% in the first six months."
    v = _variant(f"Hi Ruth,\n\n{text}\n\nDana", Claim(text=text, evidence=[]))
    assert not [i for i in deterministic_check(v, ev, config) if i.severity == "block"]
    assert v.emails[0].claims[0].evidence == ["PP:northbeam"]
    wrong = _variant(
        "Hi Ruth,\n\nNorthbeam Logistics reduced idle time by 32%.\n\nDana",
        Claim(text="Northbeam Logistics reduced idle time by 32%.", evidence=["PP:northbeam"]),
    )
    assert any(i.kind == "unknown_number" for i in deterministic_check(wrong, ev, config))


def test_llm_verifier_verdicts_are_applied(profile: CompanyProfile, config: Config) -> None:
    ids = _ids(profile)
    ev = build_evidence(profile, CONTACT, config)
    v = _variant(
        "Hi Ruth,\n\nI noticed Brightwater is hiring a Fleet Safety Manager to fix a safety crisis.\n\nDana",
        Claim(
            text="I noticed Brightwater is hiring a Fleet Safety Manager to fix a safety crisis.",
            evidence=[ids["hiring"]],
        ),
    )
    verifier = Scripted(
        {"claims": [{"id": "C1", "verdict": "unsupported", "reason": "no crisis in the evidence"}], "uncovered": []}
    )
    calls = check_variants([v], ev, config, verifier=verifier, today=TODAY)
    assert calls == 1 and v.report is not None and not v.report.passed
    assert v.emails[0].claims[0].verdict == "unsupported" and "crisis" in v.emails[0].claims[0].reason
    trimmed = trim_unsupported(v)
    assert "crisis" not in trimmed.emails[0].body


def test_evidence_ids_are_normalized(config: Config) -> None:
    offer = offer_id_map(config)
    assert normalize_evidence(["S1 (hiring)", "vp: Safety", "northbeam", "f3"], offer) == [
        "S1",
        "VP:safety",
        "PP:northbeam",
        "F3",
    ]


def test_draft_loop_regenerates_until_the_checker_passes(profile: CompanyProfile, config: Config) -> None:
    fake = build_chat_model(Settings(llm_provider="fake"))
    q = score_rules(profile, config)
    result = draft_account(profile, q, CONTACT, config, model=fake, verifier=fake, today=TODAY)
    assert len(result.variants) == 2
    for variant in result.variants:
        assert variant.report is not None and variant.report.passed
        assert all(c.verdict == "supported" for e in variant.emails for c in e.claims)
        assert len(variant.emails) == 3
    # Brightwater gets an embellishment on the first attempt from the offline "careless" drafter.
    assert any(not f.report.passed for f in result.first_drafts if f.report)
    assert any(v.attempts > 1 for v in result.variants)


def test_spam_and_format_checks() -> None:
    spammy = Email(
        step=1,
        subject="FREE TRIAL!!!",
        body="Hi {first_name}, act now for a guaranteed 100% risk-free deal! Click here: https://x.test",
    )
    issues, score, _, _ = check_email(spammy, max_words=140, banned_phrases=["guaranteed"])
    kinds = {i.kind for i in issues}
    assert {"spam_word", "banned_phrase", "placeholder", "links", "exclamation", "subject"} <= kinds
    assert score >= 0.5
    clean = Email(
        step=1,
        subject="Fleet safety at Brightwater",
        body="Hi Ruth,\n\nI noticed you are hiring a fleet safety manager. We help teams like yours coach drivers and cut harsh braking. Would a short call next week be useful to compare notes on your fleet?\n\nDana",
    )
    issues, score, ease, n = check_email(clean, max_words=140, banned_phrases=[])
    assert [i for i in issues if i.severity == "block"] == [] and score == 0 and ease > 50 and n > 30
    assert flesch_reading_ease("The cat sat on the mat.") > 90


def test_business_hours_and_weekends() -> None:
    friday_evening = dt.datetime(2026, 10, 9, 23, 30, tzinfo=dt.UTC)  # 19:30 in New York
    slot = next_business_slot(friday_evening, "America/New_York")
    local = slot.astimezone(ZoneInfo("America/New_York"))
    assert local.weekday() == 0 and local.hour == 9
    during = dt.datetime(2026, 10, 7, 14, 0, tzinfo=dt.UTC)  # Wednesday 10:00 in New York
    assert next_business_slot(during, "America/New_York") == during
    assert add_business_days(dt.date(2026, 10, 9), 3) == dt.date(2026, 10, 14)


def test_sequence_plan_spacing_and_timezones() -> None:
    approved = dt.datetime(2026, 10, 7, 20, 0, tzinfo=dt.UTC)
    for tz in ("Europe/Berlin", "America/Los_Angeles", "Asia/Tokyo"):
        times = plan_sequence(approved, tz, followup_business_days=[3, 7], key="acct")
        locals_ = [t.astimezone(ZoneInfo(tz)) for t in times]
        assert all(9 <= t.hour < 17 and t.weekday() < 5 for t in locals_), tz
        assert times[0] < times[1] < times[2]
        assert add_business_days(locals_[0].date(), 3) <= locals_[1].date()


def test_naive_datetimes_are_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_business_slot(dt.datetime(2026, 10, 7, 12, 0), "UTC")


def test_meeting_slot_overlaps_both_calendars() -> None:
    after = dt.datetime(2026, 10, 7, 18, 0, tzinfo=dt.UTC)
    start, end = find_slot(after, seller_tz="America/Los_Angeles", seller_hours=(7, 16), prospect_tz="Europe/London")  # type: ignore[misc]
    london = start.astimezone(ZoneInfo("Europe/London"))
    assert 9 <= london.hour < 17 and london.weekday() < 5 and end - start == dt.timedelta(minutes=30)
    busy = [(start, end)]
    nxt = find_slot(
        after, seller_tz="America/Los_Angeles", seller_hours=(7, 16), prospect_tz="Europe/London", busy=busy
    )
    assert nxt is not None and nxt[0] >= end
    assert find_slot(after, seller_tz="America/Los_Angeles", seller_hours=(7, 8), prospect_tz="Asia/Tokyo") is None


def test_name_check_handles_hyphens_possessives_and_titles(profile: CompanyProfile, config: Config) -> None:
    from scout.outreach.checker import _words, unknown_names

    vocabulary = _words("Harborview Shuttle won the Sea-Tac rental-car shuttle contract")
    assert (
        unknown_names(
            "Hi Grace,\n\nCongrats on the Sea-Tac contract. Wayline's VP of sales says hello.",
            vocabulary | {"wayline", "grace"},
        )
        == []
    )
    assert unknown_names("Hi Grace,\n\nI met Bob at the depot.", vocabulary) == ["Grace", "Bob"]
