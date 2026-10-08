"""Typed records that flow through the pipeline: research profile, qualification, drafts, replies."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from scout.research.injection import InjectionFinding

SignalType = Literal["hiring", "funding", "expansion", "leadership_change", "tech_stack", "competitor_in_use"]
FactField = Literal["segment", "industry", "employees", "fleet_size", "hq", "country", "founded", "served_regions"]
Route = Literal["qualified", "nurture", "disqualified"]
SIGNAL_TYPES: tuple[SignalType, ...] = (
    "hiring",
    "funding",
    "expansion",
    "leadership_change",
    "tech_stack",
    "competitor_in_use",
)
FACT_FIELDS: tuple[FactField, ...] = (
    "segment",
    "industry",
    "employees",
    "fleet_size",
    "hq",
    "country",
    "founded",
    "served_regions",
)


# --------------------------------------------------------------------------------------------- research
class Citation(BaseModel):
    url: str
    quote: str
    """Copied from the page; validated to appear on it (whitespace, quotes and case normalized)."""
    date: dt.date | None = None
    """The date printed next to the quote on the page (job posted, article date), if any."""
    page_date: dt.date | None = None


class Fact(BaseModel):
    id: str
    field: FactField
    value: str
    number: int | None = None
    citation: Citation


class Signal(BaseModel):
    id: str
    type: SignalType
    summary: str
    detail: str | None = None
    """Job title, person and role, technology..."""
    date: dt.date | None = None
    date_source: Literal["block", "quote", "page", "none"] = "none"
    age_days: int | None = None
    current: bool = False
    """Within the ICP's freshness window for this signal type, measured from the date on the page."""
    citation: Citation


class Person(BaseModel):
    name: str
    title: str
    email: str | None = None
    """Only an address printed on the page; Scout never guesses addresses."""
    citation: Citation


class PageRef(BaseModel):
    url: str
    title: str = ""
    page_date: dt.date | None = None
    fetched_at: dt.datetime


class Rejected(BaseModel):
    kind: Literal["fact", "signal", "person"]
    value: str
    url: str
    quote: str
    reason: str


class CompanyProfile(BaseModel):
    domain: str
    url: str
    name: str
    segment: str | None = None
    industry: str | None = None
    facts: list[Fact] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    people: list[Person] = Field(default_factory=list)
    injection_findings: list[InjectionFinding] = Field(default_factory=list)
    pages: list[PageRef] = Field(default_factory=list)
    skipped: list[tuple[str, str]] = Field(default_factory=list)
    rejected: list[Rejected] = Field(default_factory=list)
    """Model output that failed validation (quote not on the page, number not in the quote...)."""
    blocked_by_robots: bool = False
    mode: Literal["synthetic", "live"] = "synthetic"
    model: str = ""
    llm_calls: int = 0
    seconds: float = 0.0
    researched_at: dt.datetime | None = None

    def fact(self, field: FactField) -> Fact | None:
        return next((f for f in self.facts if f.field == field), None)

    def number(self, field: FactField) -> int | None:
        fact = self.fact(field)
        return fact.number if fact else None

    def evidence(self) -> dict[str, Citation]:
        """Every citable item by id (facts F*, signals S*)."""
        items: dict[str, Citation] = {f.id: f.citation for f in self.facts}
        items.update({s.id: s.citation for s in self.signals})
        return items


# --------------------------------------------------------------------------------------------- qualification
class RubricLine(BaseModel):
    criterion: str
    points: int
    max_points: int
    reason: str
    evidence: list[str] = Field(default_factory=list)
    """Fact / signal ids the line is based on."""


class Qualification(BaseModel):
    score: int
    route: Route
    lines: list[RubricLine]
    disqualifiers: list[str] = Field(default_factory=list)
    rules_score: int = 0
    llm_adjustment: int = 0
    llm_reason: str = ""
    llm_evidence: list[str] = Field(default_factory=list)
    mode: Literal["rules_first", "llm_only"] = "rules_first"
    model: str = ""


class Contact(BaseModel):
    name: str
    title: str
    email: str | None
    persona: str
    source: Literal["team_page", "client_list", "referral"]
    citation: Citation | None = None
    reason: str = ""


# --------------------------------------------------------------------------------------------- outreach
ClaimVerdict = Literal["supported", "unsupported", "unchecked"]


class Claim(BaseModel):
    text: str
    """The personalized phrase or sentence as it appears in the email."""
    evidence: list[str] = Field(default_factory=list)
    """Fact / signal ids (F3, S1) or offer items (PP:northbeam) the model says support it."""
    verdict: ClaimVerdict = "unchecked"
    reason: str = ""
    checked_by: list[str] = Field(default_factory=list)


class Email(BaseModel):
    step: int
    subject: str
    body: str
    claims: list[Claim] = Field(default_factory=list)


class CheckIssue(BaseModel):
    step: int
    kind: str
    """unsupported_claim, unknown_number, unknown_name, unknown_date, stale_signal, spam_word, length, readability,
    banned_phrase, subject, links, exclamation, caps..."""
    message: str
    severity: Literal["block", "warn"] = "block"
    text: str = ""


class CheckReport(BaseModel):
    passed: bool
    issues: list[CheckIssue] = Field(default_factory=list)
    spam_score: float = 0.0
    readability: float = 0.0
    words: list[int] = Field(default_factory=list)
    personalization: int = 0
    """Distinct cited facts and signals used in the first email."""


class DraftVariant(BaseModel):
    variant: str
    angle: str
    emails: list[Email]
    report: CheckReport | None = None
    attempts: int = 1
    history: list[dict[str, object]] = Field(default_factory=list)
    """Earlier attempts rejected by the checker (emails + issues), kept for review and the evaluation."""


# --------------------------------------------------------------------------------------------- replies
ReplyLabel = Literal[
    "interested",
    "meeting_request",
    "not_now",
    "referral",
    "objection",
    "unsubscribe",
    "out_of_office",
    "bounce",
]
REPLY_LABELS: tuple[ReplyLabel, ...] = (
    "interested",
    "meeting_request",
    "not_now",
    "referral",
    "objection",
    "unsubscribe",
    "out_of_office",
    "bounce",
)
ObjectionType = Literal["price", "competitor", "no_need", "timing", "authority", "trust", "other"]


class ReplyClassification(BaseModel):
    label: ReplyLabel
    objection_type: ObjectionType | None = None
    confidence: float = 0.0
    reason: str = ""
    source: Literal["rules", "llm", "rules+llm"] = "llm"
    referral_name: str | None = None
    referral_email: str | None = None
    resume_on: dt.date | None = None
    """For not_now / out_of_office: when to resume, if the reply says."""
    model: str = ""
