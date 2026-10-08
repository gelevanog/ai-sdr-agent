"""Runtime settings from environment variables (and an optional .env file). See .env.example."""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LLMProviderKind = Literal["fake", "openrouter", "qwen", "openai", "anthropic"]
CrawlMode = Literal["synthetic", "live"]
CrmMode = Literal["off", "mock", "hubspot"]

# Free OpenRouter models chosen from the smoke test before the real run (results/smoke.json).
DEFAULT_FREE_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
DEFAULT_FREE_FALLBACKS = ["nvidia/nemotron-3-ultra-550b-a55b:free"]
# Qwen Cloud (Alibaba Model Studio / DashScope), OpenAI-compatible. The international pay-as-you-go endpoint is the
# default; the Token Plan endpoint is https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1 and
# mainland China is https://dashscope.aliyuncs.com/compatible-mode/v1.
DEFAULT_QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_QWEN_MODEL = "qwen3.7-plus"
DEFAULT_MODELS: dict[str, str] = {
    "fake": "fake-rules",
    "openrouter": DEFAULT_FREE_MODEL,
    "qwen": DEFAULT_QWEN_MODEL,
    "openai": "gpt-5-mini",
    "anthropic": "claude-sonnet-5",
}


def _split(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SCOUT_", extra="ignore")

    # ---- storage
    database_url: str = "postgresql://scout:scout@localhost:5432/scout"
    seed_demo: bool = True
    """On API startup: generate the synthetic web (if missing) and load the demo accounts when the database is empty."""
    demo_seed: int = 7
    """Seed of the synthetic web generator (the company specs are hand-written; the seed picks wording and layout)."""

    # ---- ICP, offer and data
    icp_file: Path = Path("configs/icp.yaml")
    companies_file: Path = Path("data/companies.yaml")
    """Hand-written specs of the fictional companies (and the ground truth for the evaluation)."""
    synthetic_web_dir: Path = Path("data/synthetic_web")
    """Where `scout seed` writes the generated static sites (gitignored)."""
    synthetic_web_url: str = ""
    """If set (e.g. http://synthetic-web:8080), the crawler fetches the synthetic sites over HTTP from this static
    server; otherwise it reads the generated files from disk. Links in the dashboard use synthetic_web_public_url."""
    synthetic_web_public_url: str = "http://localhost:8090"
    today: dt.date | None = dt.date(2026, 10, 1)
    """The date research treats as today (fixed so signal freshness on the synthetic web is reproducible).
    Empty = the real date. Scheduling and sending always use the real clock."""

    # ---- crawler
    crawl_mode: CrawlMode = "synthetic"
    crawl_max_pages: int = 12
    crawl_min_seconds_per_domain: float = 2.0
    """Live mode: at most one request per this many seconds to the same host (synthetic mode: no delay)."""
    crawl_user_agent: str = "ScoutResearchBot/0.1 (+https://github.com/gelevanog/ai-sdr-agent; company pages only)"
    crawl_timeout_seconds: float = 15.0

    # ---- pipeline
    injection_guard: bool = True
    """Quarantine sentences that address AI agents or try to steer them, and spotlight all page text."""
    claim_checker: bool = True
    max_regenerations: int = 2
    draft_variants: int = 2
    qualification_mode: Literal["rules_first", "llm_only"] = "rules_first"
    llm_judgment: bool = True
    """Second stage of qualification: a bounded LLM adjustment on top of the deterministic rubric."""

    # ---- compliance and sending
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    """Mailpit's SMTP port. Scout only ever sends to a capture server; see README > Key design decisions."""
    smtp_capture_only: bool = True
    """Refuse to send unless the SMTP host is a known capture server (localhost, mailpit). Turning this off is a
    deliberate code change, not a setting a demo flips by accident."""
    from_address: str = "Dana Whitfield <dana@wayline.example>"
    public_url: str = "http://localhost:8000"
    """Base URL for unsubscribe links (the API serves /u/{token})."""
    daily_send_cap: int = 40
    per_domain_daily_cap: int = 2
    business_hours_start: int = 9
    business_hours_end: int = 17
    followup_business_days: Annotated[list[int], NoDecode] = Field(default_factory=lambda: [3, 7])
    retention_days_pages: int = 30
    """Raw crawled page text is deleted after this many days (the extracted, cited facts are kept)."""
    retention_days_replies: int = 180
    retention_days_rejected_drafts: int = 30
    mailpit_api_url: str = "http://localhost:8025"

    # ---- CRM
    crm_mode: CrmMode = "mock"
    hubspot_base_url: str = "https://api.hubapi.com"
    hubspot_token: str | None = Field(default=None, validation_alias=AliasChoices("HUBSPOT_TOKEN", "SCOUT_HUBSPOT_TOKEN"))

    # ---- LLM
    llm_provider: LLMProviderKind = "fake"
    llm_model: str = ""
    """Empty = the provider's default (DEFAULT_MODELS)."""
    llm_fallback_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    judge_model: str = "dots-studio/dots-3-note-preview:free"
    """A different free model for the blind preference judge and the independent claim audit in the evaluation."""
    llm_timeout_seconds: float = 180.0
    llm_max_tokens: int = 6000
    llm_temperature: float = 0.0
    llm_reasoning_effort: str = "low"
    require_free_models: bool = True
    """Refuse any OpenRouter model id that does not end in ":free" (requests and the model that answered)."""

    openrouter_api_key: str | None = Field(default=None, validation_alias=AliasChoices("OPENROUTER_API_KEY"))
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias=AliasChoices("OPENROUTER_BASE_URL")
    )
    qwen_api_key: str | None = Field(default=None, validation_alias=AliasChoices("QWEN_API_KEY", "DASHSCOPE_API_KEY"))
    qwen_base_url: str = DEFAULT_QWEN_BASE_URL
    qwen_enable_thinking: bool | None = None
    openai_api_key: str | None = Field(default=None, validation_alias=AliasChoices("OPENAI_API_KEY"))
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str | None = Field(default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY"))
    anthropic_effort: str = "low"

    # ---- budget for real API calls
    llm_cache: bool = True
    llm_cache_dir: Path = Path(".cache/llm")
    llm_ledger: Path | None = Path("results/calls.jsonl")
    llm_max_calls: int = 480
    llm_min_seconds_between_requests: float = 3.0
    llm_max_retries: int = 4

    # ---- API
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])
    results_dir: Path = Path("results")
    reviewer_name: str = "reviewer"
    """Recorded as the approver in the audit log when the API is used without sign-in (single-user demo)."""

    @field_validator("llm_fallback_models", "cors_origins", mode="before")
    @classmethod
    def _split_lists(cls, value: object) -> object:
        return _split(value)

    @field_validator("followup_business_days", mode="before")
    @classmethod
    def _split_ints(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(item) for item in value.split(",") if item.strip()]
        return value

    @field_validator("today", "llm_ledger", "qwen_enable_thinking", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value

    def research_today(self) -> dt.date:
        return self.today or dt.date.today()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
