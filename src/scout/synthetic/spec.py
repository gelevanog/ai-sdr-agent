"""The hand-written company specs in data/companies.yaml (the synthetic web's content and the evaluation's ground
truth)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

SignalType = Literal["hiring", "funding", "expansion", "leadership_change", "tech_stack", "competitor_in_use"]
Route = Literal["qualified", "nurture", "disqualified"]
PageName = Literal["about", "services", "careers", "news", "press", "team", "blog"]


class SpecSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SignalType
    page: PageName
    text: str
    """The sentence (or job title) rendered verbatim on the page."""
    date: dt.date | None = None
    title: str | None = None
    person: str | None = None
    tech: str | None = None
    current: bool = True
    """False for a signal that is too old to act on (the evaluation expects it to be found and marked stale)."""


class SpecInjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: PageName
    style: Literal["visible", "hidden"]
    text: str


class CompanySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    name: str
    segment: str
    industry: str
    employees: int
    fleet_size: int | None
    fleet_noun: str = "vehicles"
    hq: str
    country: str
    tz: str
    founded: int
    served: str
    label: Route
    why: str
    signals: list[SpecSignal] = Field(default_factory=list)
    injection: SpecInjection | None = None
    robots: Literal["allow", "disallow_team", "disallow_all"] = "allow"
    traps: list[str] = Field(default_factory=list)
    outdated: dict[str, dt.date] = Field(default_factory=dict)
    extra_jobs: list[str] = Field(default_factory=list)
    benign_ai_text: str | None = None
    bike_fleet: int | None = None

    @property
    def url(self) -> str:
        return f"https://{self.domain}/"


def load_specs(path: Path) -> list[CompanySpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    specs = [CompanySpec.model_validate(item) for item in raw]
    domains = [s.domain for s in specs]
    if len(set(domains)) != len(domains):
        raise ValueError("duplicate domains in the company specs")
    return specs
