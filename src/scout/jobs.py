"""The worker: a PostgreSQL-backed job queue (`SELECT ... FOR UPDATE SKIP LOCKED`), so the API, the worker and
the CLI share one source of truth and no extra broker is needed. Besides queued jobs, the worker sends due
messages, classifies new replies and ingests the inbox folder every tick, and runs the retention purge daily."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any

from scout.logging_config import get_logger
from scout.services import Scout, ScoutError

log = get_logger(__name__)


def run_job(scout: Scout, job: dict[str, Any]) -> dict[str, Any]:
    kind = job["kind"]
    payload = job["payload"] or {}
    account_id = int(payload.get("account_id", 0))
    if kind == "research":
        result = scout.research(account_id)
        return {"facts": len(result.profile.facts), "signals": len(result.profile.signals), "error": result.error}
    if kind == "qualify":
        q = scout.qualify(account_id)
        return {"route": q.route, "score": q.score}
    if kind == "draft":
        return {"drafts": scout.draft(account_id)}
    if kind == "pipeline":
        return scout.run_pipeline(account_id)
    if kind == "send_due":
        return scout.send_due()
    if kind == "process_inbox":
        return {"processed": scout.process_inbox()}
    if kind == "purge":
        return scout.purge()
    raise ScoutError(f"unknown job kind {kind!r}")


def tick(scout: Scout, inbox: Path | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        out["send"] = scout.send_due()
    except Exception as exc:  # Mailpit down: keep the worker alive, retry next tick
        log.warning("worker.send_failed", error=str(exc)[:200])
    if inbox is not None and inbox.is_dir():
        out["ingested"] = len(scout.ingest_folder(inbox))
    out["classified"] = scout.process_inbox()
    return out


def work(
    scout: Scout,
    *,
    poll_seconds: float = 2.0,
    tick_seconds: float = 30.0,
    inbox: Path | None = None,
    once: bool = False,
) -> None:
    last_tick = 0.0
    last_purge: dt.date | None = None
    log.info("worker.started", poll=poll_seconds, tick=tick_seconds)
    while True:
        job = scout.store.claim_job()
        if job is not None:
            started = time.monotonic()
            try:
                result = run_job(scout, job)
                scout.store.finish_job(int(job["id"]), result=_safe(result))
                log.info("job.done", id=job["id"], kind=job["kind"], seconds=round(time.monotonic() - started, 1))
            except Exception as exc:
                scout.store.finish_job(int(job["id"]), error=str(exc)[:500])
                log.warning("job.failed", id=job["id"], kind=job["kind"], error=str(exc)[:200])
            if not once:
                continue
        if time.monotonic() - last_tick >= tick_seconds:
            last_tick = time.monotonic()
            tick(scout, inbox)
            today = dt.date.today()
            if last_purge != today:
                scout.purge()
                last_purge = today
        if once:
            return
        time.sleep(poll_seconds)


def _safe(value: Any) -> Any:
    import json

    return json.loads(json.dumps(value, default=str))
