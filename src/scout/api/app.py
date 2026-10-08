"""The HTTP API for the dashboard: accounts and dossiers, the approval queue, sequences, the reply inbox, compliance
(suppression, do-not-contact, retention, unsubscribe links), settings, the audit log, CRM mock objects, CSV
exports and the evaluation results. Long-running steps are queued as jobs for the worker (or run inline with
?sync=true)."""

from __future__ import annotations

import datetime as dt
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from scout.config import get_settings
from scout.icp import Config, dump_config
from scout.jobs import run_job
from scout.logging_config import configure_logging, get_logger
from scout.models import CompanyProfile
from scout.research.extract import normalize
from scout.services import Scout, ScoutError
from scout.store.db import Store, jsonable

log = get_logger(__name__)


class AddAccount(BaseModel):
    url: str
    name: str | None = None


class EmailEdit(BaseModel):
    step: int
    subject: str
    body: str


class Approve(BaseModel):
    reviewer: str = ""
    edits: list[EmailEdit] = Field(default_factory=list)
    note: str = ""


class Reject(BaseModel):
    reviewer: str = ""
    reason: str = ""


class SendRun(BaseModel):
    fast_forward_days: float = 0.0
    """Demo only: treat this many days as having passed (messages are stamped with their scheduled time)."""


class ManualReply(BaseModel):
    from_email: str
    subject: str = ""
    body: str


class SuppressionIn(BaseModel):
    value: str
    kind: str = "email"
    reason: str = "added manually"


class Flag(BaseModel):
    flag: bool
    actor: str = ""


class IcpYaml(BaseModel):
    yaml: str


def get_scout(request: Request) -> Scout:
    scout: Scout = request.app.state.scout
    return scout


S = Annotated[Scout, Depends(get_scout)]


def create_app(scout: Scout) -> FastAPI:
    settings = scout.settings
    store = scout.store

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        store.migrate()
        if settings.seed_demo:
            added = scout.seed_demo()
            if added:
                log.info("seed.demo", accounts=added)
        yield

    app = FastAPI(title="Scout API", version="0.1.0", lifespan=lifespan)
    app.state.scout = scout
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

    def fail(exc: ScoutError) -> HTTPException:
        return HTTPException(status_code=409, detail=str(exc))

    # ------------------------------------------------------------------ health and overview
    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "accounts": store.scalar("SELECT count(*) FROM accounts")}

    @app.get("/api/overview")
    def overview(s: S) -> dict[str, Any]:
        return {
            "funnel": s.funnel(),
            "model": {
                "provider": settings.llm_provider,
                "model": settings.llm_model or "(default)",
                "free_only": settings.require_free_models,
            },
            "crawl_mode": settings.crawl_mode,
            "crm_mode": settings.crm_mode,
            "capture_only": settings.smtp_capture_only,
            "limits": s.limits.__dict__,
            "recent": jsonable(store.all("SELECT * FROM audit_log ORDER BY id DESC LIMIT 12")),
            "jobs": jsonable(store.all("SELECT status, count(*) AS n FROM jobs GROUP BY status")),
        }

    # ------------------------------------------------------------------ accounts
    @app.get("/api/accounts")
    def accounts(s: S, route: str | None = None) -> list[dict[str, Any]]:
        rows = store.all(
            "SELECT id, domain, url, name, source, status, route, score, timezone, do_not_contact, contact, error, "  # noqa: S608 - fixed clauses, values are parameters
            "profile->'signals' AS signals, profile->>'segment' AS segment, profile->'injection_findings' AS injections, "
            "qualification->'disqualifiers' AS disqualifiers, updated_at FROM accounts "
            + ("WHERE route = %s " if route else "")
            + "ORDER BY score DESC NULLS LAST, name",
            (route,) if route else (),
        )
        return jsonable(rows)

    @app.post("/api/accounts")
    def add_account(body: AddAccount, s: S) -> dict[str, Any]:
        account_id = s.add_account(body.url, body.name, source="live", actor=settings.reviewer_name)
        if account_id is None:
            raise HTTPException(409, "an account with this domain already exists")
        return {"id": account_id}

    @app.get("/api/accounts/{account_id}")
    def account(account_id: int, s: S) -> dict[str, Any]:
        try:
            acc = s.account(account_id)
        except ScoutError as exc:
            raise HTTPException(404, str(exc)) from exc
        return jsonable(
            {
                "account": acc,
                "drafts": store.all(
                    "SELECT id, variant, status, reviewer, reviewed_at, created_at, model, calls FROM drafts WHERE account_id = %s ORDER BY id DESC",
                    (account_id,),
                ),
                "messages": store.all(
                    "SELECT id, draft_id, step, to_email, subject, status, status_reason, scheduled_at, sent_at, timezone FROM messages WHERE account_id = %s ORDER BY step, id",
                    (account_id,),
                ),
                "replies": store.all(
                    "SELECT id, from_email, subject, body, label, classification, actions, received_at FROM replies WHERE account_id = %s ORDER BY id",
                    (account_id,),
                ),
                "pages": store.all(
                    "SELECT url, fetched_at, purged_at, (text IS NOT NULL) AS has_text FROM pages WHERE account_id = %s ORDER BY id",
                    (account_id,),
                ),
                "meetings": store.all("SELECT * FROM meetings WHERE account_id = %s", (account_id,)),
                "audit": store.all(
                    "SELECT * FROM audit_log WHERE entity = 'account' AND entity_id = %s ORDER BY id DESC LIMIT 30",
                    (str(account_id),),
                ),
                "source_base": settings.synthetic_web_public_url if acc["source"] == "synthetic" else None,
            }
        )

    def _job(kind: str, payload: dict[str, Any], sync: bool, s: Scout) -> dict[str, Any]:
        job_id = store.enqueue(kind, payload)
        if sync:
            job = store.claim_job()
            while job is not None and job["id"] != job_id:  # pragma: no cover - only with a busy queue
                store.run("UPDATE jobs SET status = 'queued', attempts = attempts - 1 WHERE id = %s", (job["id"],))
                job = store.claim_job()
            if job is not None:
                try:
                    result = run_job(s, job)
                    store.finish_job(job_id, result=result)
                    return {"job_id": job_id, "status": "done", "result": jsonable(result)}
                except ScoutError as exc:
                    store.finish_job(job_id, error=str(exc))
                    raise fail(exc) from exc
        return {"job_id": job_id, "status": "queued"}

    for step in ("research", "qualify", "draft", "pipeline"):

        def make(step: str = step) -> Any:
            @app.post(f"/api/accounts/{{account_id}}/{step}", name=f"account_{step}")
            def run(account_id: int, s: S, sync: bool = False) -> dict[str, Any]:
                return _job(step, {"account_id": account_id}, sync, s)

            return run

        make()

    @app.post("/api/pipeline/run-all")
    def run_all(s: S) -> dict[str, Any]:
        rows = store.all("SELECT id FROM accounts WHERE profile IS NULL ORDER BY id")
        ids = [store.enqueue("pipeline", {"account_id": r["id"]}) for r in rows]
        return {"queued": len(ids)}

    @app.post("/api/accounts/{account_id}/do-not-contact")
    def do_not_contact(account_id: int, body: Flag, s: S) -> dict[str, Any]:
        return {"cancelled": s.set_do_not_contact(account_id, body.flag, actor=body.actor or settings.reviewer_name)}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: int) -> dict[str, Any]:
        row = store.one("SELECT * FROM jobs WHERE id = %s", (job_id,))
        if row is None:
            raise HTTPException(404, "job not found")
        return jsonable(row)

    @app.get("/api/source")
    def source(account_id: int, url: str, quote: str = "") -> dict[str, Any]:
        """The stored page text behind a citation, with the quote located (for the dossier and review screens)."""
        row = store.one(
            "SELECT url, fetched_at, text, purged_at FROM pages WHERE account_id = %s AND url = %s", (account_id, url)
        )
        if row is None:
            raise HTTPException(404, "page not crawled")
        text = row["text"] or ""
        found = bool(quote) and normalize(quote) in normalize(text)
        return jsonable({**row, "quote_found": found, "purged": row["purged_at"] is not None})

    # ------------------------------------------------------------------ drafts and approval
    @app.get("/api/drafts")
    def drafts(status: str = "pending") -> list[dict[str, Any]]:
        rows = store.all(
            "SELECT d.id, d.account_id, d.variant, d.status, d.contact, d.created_at, d.reviewer, d.reviewed_at, "
            "d.data->'report' AS report, d.data->'attempts' AS attempts, d.data->'emails'->0->>'subject' AS subject, "
            "a.name, a.domain, a.score, a.timezone FROM drafts d JOIN accounts a ON a.id = d.account_id "
            "WHERE d.status = %s ORDER BY a.score DESC NULLS LAST, d.account_id, d.variant",
            (status,),
        )
        return jsonable(rows)

    @app.get("/api/drafts/{draft_id}")
    def draft(draft_id: int, s: S) -> dict[str, Any]:
        row = store.one("SELECT * FROM drafts WHERE id = %s", (draft_id,))
        if row is None:
            raise HTTPException(404, "draft not found")
        acc = s.account(row["account_id"])
        siblings = store.all(
            "SELECT id, variant, status FROM drafts WHERE account_id = %s AND created_at = %s ORDER BY variant",
            (row["account_id"], row["created_at"]),
        )
        return jsonable(
            {
                "draft": row,
                "account": {
                    k: acc[k]
                    for k in ("id", "domain", "name", "url", "score", "route", "timezone", "source", "qualification")
                },
                "profile": acc["profile"],
                "siblings": siblings,
                "feedback": store.all("SELECT * FROM feedback WHERE draft_id = %s ORDER BY id", (draft_id,)),
                "messages": store.all(
                    "SELECT id, step, status, scheduled_at, sent_at, timezone, status_reason FROM messages WHERE draft_id = %s ORDER BY step",
                    (draft_id,),
                ),
                "seller": {
                    "company": s.config.seller.company,
                    "postal_address": s.config.seller.postal_address,
                    "from": settings.from_address,
                    "offer": {
                        **{f"VP:{vp.id}": vp.text for vp in s.config.seller.value_props},
                        **{f"PP:{pp.id}": pp.text for pp in s.config.seller.proof_points},
                    },
                },
                "source_base": settings.synthetic_web_public_url if acc["source"] == "synthetic" else None,
            }
        )

    @app.post("/api/drafts/{draft_id}/approve")
    def approve(draft_id: int, body: Approve, s: S) -> dict[str, Any]:
        try:
            return s.approve(
                draft_id,
                reviewer=body.reviewer or settings.reviewer_name,
                edits=[e.model_dump() for e in body.edits],
                note=body.note,
            )
        except ScoutError as exc:
            raise fail(exc) from exc

    @app.post("/api/drafts/{draft_id}/reject")
    def reject(draft_id: int, body: Reject, s: S) -> dict[str, Any]:
        try:
            s.reject(draft_id, reviewer=body.reviewer or settings.reviewer_name, reason=body.reason)
        except ScoutError as exc:
            raise fail(exc) from exc
        return {"ok": True}

    @app.get("/api/feedback")
    def feedback() -> list[dict[str, Any]]:
        return jsonable(
            store.all(
                "SELECT f.*, a.name FROM feedback f JOIN drafts d ON d.id = f.draft_id JOIN accounts a ON a.id = d.account_id ORDER BY f.id DESC"
            )
        )

    # ------------------------------------------------------------------ sequences and sending
    @app.get("/api/messages")
    def messages() -> list[dict[str, Any]]:
        return jsonable(
            store.all(
                "SELECT m.id, m.draft_id, m.account_id, m.step, m.to_email, m.to_name, m.subject, m.status, m.status_reason, m.scheduled_at, m.sent_at, "
                "m.timezone, m.approved_by, a.name FROM messages m JOIN accounts a ON a.id = m.account_id ORDER BY m.draft_id DESC, m.step"
            )
        )

    @app.post("/api/send/run")
    def send_run(body: SendRun, s: S) -> dict[str, Any]:
        as_of = s.now() + dt.timedelta(days=body.fast_forward_days) if body.fast_forward_days > 0 else None
        try:
            return s.send_due(as_of)
        except Exception as exc:  # e.g. Mailpit not reachable
            raise HTTPException(502, f"sending failed: {exc}") from exc

    # ------------------------------------------------------------------ replies
    @app.get("/api/replies")
    def replies() -> list[dict[str, Any]]:
        return jsonable(
            store.all(
                "SELECT r.*, a.name, a.domain, m.step, m.subject AS sent_subject FROM replies r LEFT JOIN accounts a ON a.id = r.account_id "
                "LEFT JOIN messages m ON m.id = r.message_id ORDER BY r.received_at DESC, r.id DESC"
            )
        )

    @app.post("/api/replies/simulate")
    def simulate(s: S, count: int = 0) -> dict[str, Any]:
        ids = s.simulate_replies(count=count or None)
        processed = s.process_inbox()
        return {"created": len(ids), "processed": processed}

    @app.post("/api/replies")
    def manual_reply(body: ManualReply, s: S) -> dict[str, Any]:
        reply_id = s.ingest_reply(from_email=body.from_email, subject=body.subject, body=body.body, source="manual")
        result = s.classify(reply_id)
        return {"id": reply_id, "label": result.label}

    @app.post("/api/replies/process")
    def process(s: S) -> dict[str, Any]:
        return {"processed": s.process_inbox()}

    @app.get("/api/meetings")
    def meetings() -> list[dict[str, Any]]:
        return jsonable(
            store.all("SELECT m.*, a.name FROM meetings m JOIN accounts a ON a.id = m.account_id ORDER BY starts_at")
        )

    # ------------------------------------------------------------------ compliance
    @app.get("/api/suppression")
    def suppression() -> list[dict[str, Any]]:
        return jsonable(store.all("SELECT * FROM suppression ORDER BY created_at DESC"))

    @app.post("/api/suppression")
    def add_suppression(body: SuppressionIn, s: S) -> dict[str, Any]:
        from scout import compliance

        if body.kind == "domain":
            cancelled = compliance.suppress_domain(
                store, body.value, reason=body.reason, source="manual", actor=settings.reviewer_name
            )
        else:
            cancelled = compliance.suppress_email(
                store, body.value, reason=body.reason, source="manual", actor=settings.reviewer_name
            )
        return {"cancelled": cancelled}

    @app.post("/api/retention/purge")
    def purge(s: S) -> dict[str, int]:
        return s.purge()

    @app.get("/u/{token}", response_class=HTMLResponse)
    def unsubscribe_page(token: str) -> str:
        return (
            "<!doctype html><meta charset='utf-8'><title>Unsubscribe</title>"
            "<body style='font-family:system-ui;max-width:520px;margin:60px auto'>"
            "<h2>Stop these emails?</h2><p>Press the button and you will not hear from us again.</p>"
            f"<form method='post' action='/u/{token}'><button style='padding:10px 18px'>Unsubscribe</button></form></body>"
        )

    @app.post("/u/{token}", response_class=HTMLResponse)
    def unsubscribe(token: str) -> str:
        from scout import compliance

        result = compliance.unsubscribe(store, token, source="link")
        if result is None:
            raise HTTPException(404, "unknown link")
        return "<!doctype html><meta charset='utf-8'><body style='font-family:system-ui;max-width:520px;margin:60px auto'><h2>You are unsubscribed.</h2><p>We will not email this address again.</p></body>"

    # ------------------------------------------------------------------ settings
    @app.get("/api/settings/icp")
    def get_icp(s: S) -> dict[str, Any]:
        override = store.get_setting("icp_yaml")
        return {
            "yaml": override or settings.icp_file.read_text(encoding="utf-8"),
            "overridden": bool(override),
            "config": s.config.model_dump(mode="json"),
        }

    @app.put("/api/settings/icp")
    def put_icp(body: IcpYaml) -> dict[str, Any]:
        try:
            config = Config.model_validate(yaml.safe_load(body.yaml))
        except Exception as exc:
            raise HTTPException(422, f"invalid ICP YAML: {str(exc)[:400]}") from exc
        store.set_setting("icp_yaml", dump_config(config))
        store.audit(
            settings.reviewer_name, "settings.icp_updated", "settings", "icp", {"segments": config.icp.target_segments}
        )
        return {"ok": True}

    @app.get("/api/settings/compliance")
    def get_compliance(s: S) -> dict[str, Any]:
        return {**s.limits.__dict__, "capture_only": settings.smtp_capture_only, "smtp_host": settings.smtp_host}

    @app.put("/api/settings/compliance")
    def put_compliance(s: S, body: Annotated[dict[str, int], Body()]) -> dict[str, Any]:
        allowed = set(s.limits.__dict__)
        clean = {k: max(0, int(v)) for k, v in body.items() if k in allowed}
        store.set_setting("compliance", clean)
        store.audit(settings.reviewer_name, "settings.compliance_updated", "settings", "compliance", clean)
        return s.limits.__dict__

    # ------------------------------------------------------------------ audit, CRM, exports, evaluation
    @app.get("/api/audit")
    def audit(limit: int = Query(200, le=2000), action: str | None = None) -> list[dict[str, Any]]:
        if action:
            return jsonable(
                store.all(
                    "SELECT * FROM audit_log WHERE action LIKE %s ORDER BY id DESC LIMIT %s", (action + "%", limit)
                )
            )
        return jsonable(store.all("SELECT * FROM audit_log ORDER BY id DESC LIMIT %s", (limit,)))

    @app.get("/api/crm/objects")
    def crm_objects() -> list[dict[str, Any]]:
        return jsonable(store.all("SELECT * FROM mock_crm ORDER BY id DESC LIMIT 500"))

    @app.get("/api/export/{kind}.csv", response_class=PlainTextResponse)
    def export(kind: str, s: S) -> PlainTextResponse:
        try:
            text = s.export_csv(kind)
        except ScoutError as exc:
            raise HTTPException(404, str(exc)) from exc
        return PlainTextResponse(
            text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="scout-{kind}.csv"'}
        )

    @app.get("/api/evaluation")
    def evaluation() -> dict[str, Any]:
        path = settings.results_dir / "summary.json"
        if not path.exists():
            return {"available": False}
        return {"available": True, **json.loads(path.read_text(encoding="utf-8"))}

    @app.get("/api/profile/{account_id}/evidence")
    def evidence(account_id: int, s: S) -> dict[str, Any]:
        profile = CompanyProfile.model_validate(s.account(account_id)["profile"])
        return {k: v.model_dump(mode="json") for k, v in profile.evidence().items()}

    return app


def create_default_app() -> FastAPI:
    settings = get_settings()
    configure_logging(fmt="json" if Path("/.dockerenv").exists() else "console")
    store = Store(settings.database_url)
    return create_app(Scout(settings, store))
