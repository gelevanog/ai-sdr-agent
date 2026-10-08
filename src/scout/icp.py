"""The ICP and offer configuration (configs/icp.yaml), loaded strictly: unknown keys fail at startup."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValueProp(_Strict):
    id: str
    text: str
    fits_signals: list[str] = Field(default_factory=list)


class ProofPoint(_Strict):
    id: str
    text: str


class Seller(_Strict):
    company: str
    product: str
    one_liner: str
    sender_name: str
    sender_title: str
    sender_email: str
    postal_address: str
    website: str
    value_props: list[ValueProp]
    proof_points: list[ProofPoint]
    integrations: list[str]
    customers: list[str]
    competitors: list[str]
    meeting_minutes: int = 30
    meeting_timezone: str = "UTC"
    meeting_hours: tuple[int, int] = (9, 17)


class Range(_Strict):
    min: int
    max: int | None = None
    hard_min: int = 0


class Regions(_Strict):
    allowed: list[str]


class SignalConfig(_Strict):
    fleet_roles: list[str]
    windows_days: dict[str, int]


class ICP(_Strict):
    target_segments: list[str]
    excluded_segments: list[str]
    employees: Range
    fleet: Range
    regions: Regions
    signals: SignalConfig


class Routes(_Strict):
    qualified: int
    nurture: int


class Rubric(_Strict):
    firmographic: dict[str, int]
    signals: dict[str, int]
    signals_cap: int
    llm_adjustment_max: int
    routes: Routes
    min_facts_for_decision: int = 3
    leadership_titles: list[str] = Field(default_factory=list)


class Persona(_Strict):
    id: str
    titles: list[str]


class Outreach(_Strict):
    tone: str
    max_words_first: int
    max_words_followup: int
    sequence_steps: int = 3
    cta: str
    banned_phrases: list[str] = Field(default_factory=list)


class Config(_Strict):
    seller: Seller
    icp: ICP
    rubric: Rubric
    personas: list[Persona]
    never_contact_titles: list[str] = Field(default_factory=list)
    outreach: Outreach

    @property
    def segments(self) -> list[str]:
        return [*self.icp.target_segments, *self.icp.excluded_segments]


def load_config(path: Path) -> Config:
    return Config.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def dump_config(config: Config) -> str:
    return yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=120)
