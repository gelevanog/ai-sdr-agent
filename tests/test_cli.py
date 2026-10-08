"""CLI smoke tests with the offline model. Needs TEST_DATABASE_URL."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.config import get_settings

pytestmark = pytest.mark.db


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, db_url: str, web_dir: Path, tmp_path: Path) -> None:
    from scout.store.db import Store

    store = Store(db_url)
    store.reset()
    store.close()
    for key, value in {
        "SCOUT_DATABASE_URL": db_url,
        "SCOUT_LLM_PROVIDER": "fake",
        "SCOUT_LLM_LEDGER": "",
        "SCOUT_SYNTHETIC_WEB_DIR": str(web_dir),
        "SCOUT_RESULTS_DIR": str(tmp_path),
        "SCOUT_SMTP_HOST": "localhost",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_cli_end_to_end(env: None, tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert "accounts added: 60" in runner.invoke(app, ["seed"]).output
    out = runner.invoke(app, ["pipeline", "-a", "1", "-a", "2"])
    assert out.exit_code == 0 and '"route": "qualified"' in out.output
    queue = runner.invoke(app, ["queue"])
    assert queue.exit_code == 0 and "Brightwater" in queue.output
    out = runner.invoke(app, ["qualify", "--all"])
    assert out.exit_code == 0 and "qualified" in out.output  # only researched accounts are re-scored
    assert runner.invoke(app, ["approve", "1"]).exit_code != 0, "--reviewer is required"
    assert runner.invoke(app, ["approve", "1", "--reviewer", "ivan"]).exit_code == 0
    assert runner.invoke(app, ["suppress", "nobody@nowhere.example"]).exit_code == 0
    assert runner.invoke(app, ["purge"]).exit_code == 0
    export = tmp_path / "accounts.csv"
    assert runner.invoke(
        app, ["export", "accounts", "--out", str(export)]
    ).exit_code == 0 and export.read_text().startswith("domain,")
    result = runner.invoke(app, ["eval", "claims", "--name", "cli"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "cli_claims.json").exists()
    get_settings.cache_clear()
