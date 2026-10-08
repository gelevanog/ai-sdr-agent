"""Shared fixtures. No test needs an API key: the offline model answers every task. Database tests need
TEST_DATABASE_URL (e.g. `make db`); they are skipped without it unless REQUIRE_TEST_DB=1 (CI), which turns a missing
database into a failure."""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from scout.config import Settings
from scout.icp import Config, load_config
from scout.mailer import MemoryMailer
from scout.research.crawler import Crawler, FileFetcher
from scout.synthetic.generator import write_web
from scout.synthetic.spec import CompanySpec, load_specs

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TODAY = dt.date(2026, 10, 1)


@pytest.fixture(scope="session")
def specs() -> list[CompanySpec]:
    return load_specs(ROOT / "data" / "companies.yaml")


@pytest.fixture(scope="session")
def config() -> Config:
    return load_config(ROOT / "configs" / "icp.yaml")


@pytest.fixture(scope="session")
def web_dir(tmp_path_factory: pytest.TempPathFactory, specs: list[CompanySpec]) -> Path:
    out = tmp_path_factory.mktemp("synthetic_web")
    write_web(specs, out, 7)
    return out


@pytest.fixture
def crawler(web_dir: Path) -> Crawler:
    return Crawler(FileFetcher(web_dir), user_agent="ScoutResearchBot")


def make_settings(web_dir: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "llm_provider": "fake",
        "llm_ledger": None,
        "llm_cache": False,
        "synthetic_web_dir": web_dir,
        "companies_file": ROOT / "data" / "companies.yaml",
        "icp_file": ROOT / "configs" / "icp.yaml",
        "crm_mode": "mock",
        "seed_demo": False,
        "results_dir": ROOT / "results",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
def settings(web_dir: Path) -> Settings:
    return make_settings(web_dir)


def _db_url() -> str | None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url and os.environ.get("REQUIRE_TEST_DB") == "1":
        pytest.fail("REQUIRE_TEST_DB=1 but TEST_DATABASE_URL is not set")
    return url


@pytest.fixture(scope="session")
def db_url() -> str:
    url = _db_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


@pytest.fixture
def store(db_url: str) -> Iterator[object]:
    from scout.store.db import Store

    s = Store(db_url)
    s.reset()
    yield s
    s.close()


@pytest.fixture
def mailer() -> MemoryMailer:
    return MemoryMailer()


@pytest.fixture
def scout(store: object, web_dir: Path, mailer: MemoryMailer, db_url: str) -> object:
    from scout.services import Scout

    app = Scout(make_settings(web_dir, database_url=db_url), store, mailer=mailer)  # type: ignore[arg-type]
    app.seed_demo()
    return app


@pytest.fixture
def pipeline(scout: object) -> object:
    """Every synthetic account researched, qualified and drafted with the offline model."""
    from scout.services import Scout

    assert isinstance(scout, Scout)
    for row in scout.store.all("SELECT id FROM accounts ORDER BY id"):
        scout.run_pipeline(int(row["id"]))
    return scout
