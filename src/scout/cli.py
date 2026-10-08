"""scout seed | research | qualify | draft | pipeline | approve | reject | send | replies | suppress | purge |
export | eval | serve | worker"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from scout.config import Settings, get_settings
from scout.logging_config import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Scout: an AI SDR with citations, a claim checker and human approval.")
replies_app = typer.Typer(no_args_is_help=True, help="Reply inbox: simulate, ingest a folder of .eml files, classify.")
app.add_typer(replies_app, name="replies")
console = Console()


def _scout(settings: Settings | None = None):  # type: ignore[no-untyped-def]
    from scout.services import Scout
    from scout.store.db import Store

    settings = settings or get_settings()
    store = Store(settings.database_url)
    store.migrate()
    return Scout(settings, store)


def _ids(scout, account: list[int] | None, all_: bool, where: str = "TRUE") -> list[int]:  # type: ignore[no-untyped-def]
    if account:
        return account
    if not all_:
        raise typer.BadParameter("pass --account ID (repeatable) or --all")
    return [int(r["id"]) for r in scout.store.all(f"SELECT id FROM accounts WHERE {where} ORDER BY id")]  # noqa: S608 - fixed clauses


Account = Annotated[list[int] | None, typer.Option("--account", "-a", help="Account id (repeatable).")]
All = Annotated[bool, typer.Option("--all", help="Every account.")]


@app.callback()
def main(log_level: Annotated[str, typer.Option(help="Log level.")] = "WARNING") -> None:
    configure_logging(level=log_level)


@app.command()
def seed(
    force: Annotated[bool, typer.Option(help="Regenerate the synthetic web and reload accounts.")] = False,
    reset: Annotated[bool, typer.Option(help="Drop and recreate every table first (deletes all data).")] = False,
) -> None:
    """Generate the synthetic web (60 fictional companies) and load them as target accounts."""
    scout = _scout()
    if reset:
        scout.store.reset()
    files = scout.ensure_synthetic_web(force=force)
    added = scout.seed_demo(force=force)
    console.print(f"synthetic web: {files or 'already generated'} files; accounts added: {added}")


@app.command("add")
def add_account(url: str, name: Annotated[str | None, typer.Option()] = None) -> None:
    """Add a live-mode target (a company website you are allowed to research)."""
    scout = _scout()
    account_id = scout.add_account(url, name, source="live", actor="cli")
    console.print(f"account {account_id}" if account_id else "already exists")


@app.command("contacts")
def import_contacts(csv_file: Path) -> None:
    """Live mode: import client-supplied business contacts (CSV columns: domain,name,title,email)."""
    scout = _scout()
    with csv_file.open(encoding="utf-8") as handle:
        total = scout.import_client_contacts(list(csv.DictReader(handle)), actor="cli")
    console.print(f"{total} client contacts on file")


@app.command()
def research(account: Account = None, all_: All = False, no_guard: Annotated[bool, typer.Option(help="Ablation: no injection guard.")] = False) -> None:
    """Crawl and extract cited facts and signals."""
    scout = _scout()
    for account_id in _ids(scout, account, all_):
        result = scout.research(account_id, guard=False if no_guard else None)
        p = result.profile
        console.print(f"{account_id} {p.domain}: {len(p.pages)} pages, {len(p.facts)} facts, {len(p.signals)} signals, {len(p.rejected)} rejected, {len(p.injection_findings)} injection findings {result.error or ''}")


@app.command()
def qualify(account: Account = None, all_: All = False) -> None:
    """Score against the ICP rubric (rules first, bounded LLM judgment) and pick a contact."""
    scout = _scout()
    table = Table("id", "account", "route", "score", "disqualifiers / adjustment")
    for account_id in _ids(scout, account, all_, "profile IS NOT NULL"):
        q = scout.qualify(account_id)
        table.add_row(str(account_id), scout.account(account_id)["name"], q.route, str(q.score), "; ".join(q.disqualifiers) or (f"{q.llm_adjustment:+d} {q.llm_reason[:60]}" if q.llm_adjustment else ""))
    console.print(table)


@app.command()
def draft(account: Account = None, all_: All = False, no_checker: Annotated[bool, typer.Option(help="Ablation: skip the claim checker.")] = False) -> None:
    """Draft the email and two follow-ups (A/B variants) for qualified accounts."""
    from scout.services import ScoutError

    scout = _scout()
    for account_id in _ids(scout, account, all_, "route = 'qualified'"):
        try:
            ids = scout.draft(account_id, checker=False if no_checker else None)
            console.print(f"{account_id}: drafts {ids}")
        except ScoutError as exc:
            console.print(f"{account_id}: [yellow]{exc}[/yellow]")


@app.command()
def pipeline(account: Account = None, all_: All = False) -> None:
    """Research, qualify and draft in one go."""
    scout = _scout()
    for account_id in _ids(scout, account, all_):
        console.print(json.dumps(scout.run_pipeline(account_id), default=str))


@app.command()
def approve(draft_id: int, reviewer: Annotated[str, typer.Option(help="Your name (recorded in the audit log).")]) -> None:
    """Approve a draft as written and schedule its sequence."""
    scout = _scout()
    console.print_json(json.dumps(scout.approve(draft_id, reviewer=reviewer)))


@app.command()
def reject(draft_id: int, reviewer: Annotated[str, typer.Option()], reason: Annotated[str, typer.Option()] = "") -> None:
    """Reject a draft."""
    _scout().reject(draft_id, reviewer=reviewer, reason=reason)


@app.command()
def queue() -> None:
    """List drafts waiting for approval."""
    scout = _scout()
    table = Table("draft", "account", "variant", "subject", "checker")
    for r in scout.store.all("SELECT d.id, a.name, d.variant, d.data FROM drafts d JOIN accounts a ON a.id = d.account_id WHERE d.status = 'pending' ORDER BY d.id"):
        report = r["data"].get("report") or {}
        table.add_row(str(r["id"]), r["name"], r["variant"], r["data"]["emails"][0]["subject"], "passed" if report.get("passed") else "issues")
    console.print(table)


@app.command()
def send(fast_forward_days: Annotated[float, typer.Option(help="Demo: treat N days as passed.")] = 0.0) -> None:
    """Send approved messages that are due (to the capture SMTP server only)."""
    scout = _scout()
    as_of = scout.now() + dt.timedelta(days=fast_forward_days) if fast_forward_days else None
    console.print(scout.send_due(as_of))


@replies_app.command("simulate")
def replies_simulate(count: Annotated[int, typer.Option()] = 0) -> None:
    """Drop hand-written replies into the inbox for sent emails, then classify and route them."""
    scout = _scout()
    ids = scout.simulate_replies(count=count or None)
    console.print(f"{len(ids)} replies created, {scout.process_inbox()} classified")


@replies_app.command("ingest")
def replies_ingest(folder: Path) -> None:
    """Ingest .eml files from a folder (renamed to .eml.done), then classify them."""
    scout = _scout()
    ids = scout.ingest_folder(folder)
    console.print(f"{len(ids)} replies ingested, {scout.process_inbox()} classified")


@replies_app.command("process")
def replies_process() -> None:
    """Classify and route unclassified replies."""
    console.print(f"{_scout().process_inbox()} classified")


@app.command()
def suppress(value: str, domain: Annotated[bool, typer.Option(help="Suppress a whole domain.")] = False, reason: str = "added manually") -> None:
    """Add an address (or a domain) to the suppression list and cancel its scheduled messages."""
    from scout import compliance

    scout = _scout()
    fn = compliance.suppress_domain if domain else compliance.suppress_email
    console.print(f"cancelled {fn(scout.store, value, reason=reason, source='cli', actor='cli')} scheduled message(s)")


@app.command()
def purge() -> None:
    """Apply the data-retention settings now."""
    console.print(_scout().purge())


@app.command()
def export(kind: str, out: Annotated[Path | None, typer.Option()] = None) -> None:
    """CSV export: accounts, contacts or activities."""
    text = _scout().export_csv(kind)
    if out:
        out.write_text(text, encoding="utf-8")
    else:
        console.print(text, markup=False, highlight=False)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000) -> None:  # noqa: S104 - a dev server
    """Run the API."""
    import uvicorn

    uvicorn.run("scout.api.app:create_default_app", factory=True, host=host, port=port)


@app.command()
def worker(once: Annotated[bool, typer.Option(help="Process one job (or one tick) and exit.")] = False, inbox: Annotated[Path | None, typer.Option(help="Folder with .eml replies.")] = None) -> None:
    """Run the background worker (queued jobs, due sends, reply classification, retention)."""
    from scout.jobs import work

    work(_scout(), inbox=inbox, once=once)


@app.command("eval")
def evaluate(
    suite: Annotated[str, typer.Argument(help="all | research | qualification | drafts | replies | compliance | claims | injection | summary | free-models | smoke")] = "all",
    name: Annotated[str, typer.Option(help="Run name (results/<name>_*.json).")] = "offline",
    provider: Annotated[str | None, typer.Option()] = None,
    model: Annotated[str | None, typer.Option()] = None,
    fallbacks: Annotated[str | None, typer.Option(help="Comma-separated free fallbacks.")] = None,
    limit: Annotated[int, typer.Option(help="Only the first N items (0 = all).")] = 0,
    subset: Annotated[bool, typer.Option(help="The stratified subset used for ablations and model comparison.")] = False,
    no_checker: Annotated[bool, typer.Option()] = False,
    llm_only: Annotated[bool, typer.Option(help="Qualification ablation: LLM only, no rules.")] = False,
    no_rules: Annotated[bool, typer.Option(help="Reply ablation: LLM only, no rules.")] = False,
) -> None:
    """Run the evaluation suites (offline by default; see README > Results)."""
    from scout.eval import runner

    runner.main(
        suite,
        name=name,
        provider=provider,
        model=model,
        fallbacks=[f for f in (fallbacks or "").split(",") if f],
        limit=limit,
        subset=subset,
        no_checker=no_checker,
        llm_only=llm_only,
        no_rules=no_rules,
    )


if __name__ == "__main__":  # pragma: no cover
    app()
