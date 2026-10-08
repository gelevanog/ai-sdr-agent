"""The service against PostgreSQL: approval gate, suppression across sequences, caps, do-not-contact, retention,
replies routing, CRM sync to the mock, exports, the audit log. Needs TEST_DATABASE_URL."""

from __future__ import annotations

import datetime as dt
from typing import Any

import psycopg
import pytest

from scout import compliance
from scout.mailer import CaptureOnlyError, MemoryMailer, SmtpMailer
from scout.services import Scout, ScoutError

pytestmark = pytest.mark.db


def _pending(app: Scout, account_id: int | None = None) -> dict[str, Any]:
    sql = (
        "SELECT * FROM drafts WHERE status = 'pending'"
        + (" AND account_id = %s" if account_id else "")
        + " ORDER BY id LIMIT 1"
    )
    row = app.store.one(sql, (account_id,) if account_id else ())
    assert row is not None
    return row


def test_pipeline_routes_and_drafts(pipeline: Scout) -> None:
    funnel = pipeline.funnel()
    assert funnel["accounts"] == 60 and funnel["researched"] == 60
    assert funnel["qualified"] == 22 and funnel["disqualified"] == 21
    assert funnel["drafted"] == 21  # one qualified account has no reachable contact (robots.txt hides its team page)
    assert funnel["injection_findings"] == 2
    assert pipeline.store.scalar("SELECT count(*) FROM messages") == 0, "nothing is scheduled before approval"


def test_unapproved_messages_never_send(pipeline: Scout, mailer: MemoryMailer) -> None:
    draft = _pending(pipeline)
    pipeline.store.run(
        "INSERT INTO messages (draft_id, account_id, step, to_email, to_name, subject, body, timezone, scheduled_at, approved, unsubscribe_token) "
        "VALUES (%s, %s, 1, 'x@y.example', 'X', 's', 'b', 'UTC', now() - interval '1 hour', false, 'tok-1')",
        (draft["id"], draft["account_id"]),
    )
    assert pipeline.send_due()["sent"] == 0 and mailer.sent == []
    with pytest.raises(psycopg.errors.CheckViolation):
        pipeline.store.run("UPDATE messages SET status = 'sent' WHERE unsubscribe_token = 'tok-1'")


def test_approval_with_edits_schedules_and_sends(pipeline: Scout, mailer: MemoryMailer) -> None:
    draft = _pending(pipeline)
    before = draft["data"]["emails"][0]["body"]
    edited = before.replace("Hi ", "Hello ", 1) + "\nP.S. We met at the 2031 expo."
    result = pipeline.approve(
        int(draft["id"]), reviewer="ivan", edits=[{"step": 1, "subject": "New subject", "body": edited}]
    )
    assert len(result["scheduled"]) == 3 and result["edits"][0]["chars_added"] > 0
    assert any("2031" in w for w in result["warnings"]), "an unverifiable detail added by the reviewer is pointed out"
    feedback = pipeline.store.one("SELECT * FROM feedback WHERE draft_id = %s", (draft["id"],))
    assert feedback is not None and "+P.S." in feedback["diff"]
    siblings = pipeline.store.all(
        "SELECT status FROM drafts WHERE account_id = %s AND id <> %s", (draft["account_id"], draft["id"])
    )
    assert {s["status"] for s in siblings} == {"superseded"}
    counts = pipeline.send_due(pipeline.now() + dt.timedelta(days=20))
    assert counts["sent"] == 3
    first = mailer.sent[0]
    assert first["Subject"] == "New subject" and "Hello " in first.get_content()
    assert first["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click" and "/u/" in first["List-Unsubscribe"]
    assert pipeline.config.seller.postal_address in first.get_content()
    assert mailer.sent[1]["In-Reply-To"] == first["Message-ID"] and mailer.sent[1]["Subject"].startswith("Re: ")
    with pytest.raises(ScoutError):
        pipeline.approve(int(draft["id"]), reviewer="ivan")


def test_unsubscribe_link_stops_every_sequence_to_the_address(pipeline: Scout) -> None:
    draft = _pending(pipeline)
    pipeline.approve(int(draft["id"]), reviewer="ivan")
    pipeline.draft(int(draft["account_id"]))
    pipeline.approve(int(_pending(pipeline, int(draft["account_id"]))["id"]), reviewer="ivan")
    email = draft["contact"]["email"]
    assert pipeline.store.scalar("SELECT count(DISTINCT draft_id) FROM messages WHERE to_email = %s", (email,)) == 2
    token = pipeline.store.scalar(
        "SELECT unsubscribe_token FROM messages WHERE to_email = %s ORDER BY id LIMIT 1", (email,)
    )
    assert compliance.unsubscribe(pipeline.store, str(token)) is not None
    assert (
        pipeline.store.scalar("SELECT count(*) FROM messages WHERE to_email = %s AND status = 'scheduled'", (email,))
        == 0
    )
    assert pipeline.send_due(pipeline.now() + dt.timedelta(days=30))["sent"] == 0
    with pytest.raises(ScoutError, match="suppressed"):
        pipeline.draft(int(draft["account_id"]))


def test_suppression_trigger_and_domain_suppression(pipeline: Scout) -> None:
    draft = _pending(pipeline)
    domain = draft["contact"]["email"].split("@")[1]
    compliance.suppress_domain(pipeline.store, domain, reason="client asked", source="test", actor="test")
    with pytest.raises(ScoutError, match="suppression"):
        pipeline.approve(int(draft["id"]), reviewer="ivan")
    pipeline.store.run(
        "INSERT INTO messages (draft_id, account_id, step, to_email, to_name, subject, body, timezone, scheduled_at, approved, approved_by, unsubscribe_token) "
        "VALUES (%s, %s, 1, %s, 'X', 's', 'b', 'UTC', now(), true, 'ivan', 'tok-2')",
        (draft["id"], draft["account_id"], f"anyone@{domain}"),
    )
    assert pipeline.store.scalar("SELECT status FROM messages WHERE unsubscribe_token = 'tok-2'") == "blocked"


def test_caps_defer_instead_of_dropping(pipeline: Scout) -> None:
    pipeline.store.set_setting("compliance", {"daily_send_cap": 1})
    drafts = pipeline.store.all(
        "SELECT DISTINCT ON (account_id) id FROM drafts WHERE status = 'pending' ORDER BY account_id, id LIMIT 3"
    )
    for d in drafts:
        pipeline.approve(int(d["id"]), reviewer="ivan")
    for _ in range(40):
        result = pipeline.send_due(pipeline.now() + dt.timedelta(days=40))
        if not result["sent"] and not result["deferred"]:
            break
    per_day = pipeline.store.all(
        "SELECT date_trunc('day', sent_at) AS d, count(*) AS n FROM messages WHERE status = 'sent' GROUP BY 1"
    )
    assert sum(r["n"] for r in per_day) == 9 and max(r["n"] for r in per_day) == 1
    assert pipeline.store.scalar("SELECT count(*) FROM audit_log WHERE action = 'message.deferred'") > 0


def test_do_not_contact_and_retention(pipeline: Scout) -> None:
    draft = _pending(pipeline)
    pipeline.approve(int(draft["id"]), reviewer="ivan")
    assert pipeline.set_do_not_contact(int(draft["account_id"]), True, actor="ivan") == 3
    assert pipeline.send_due(pipeline.now() + dt.timedelta(days=30))["sent"] == 0
    pipeline.store.run("UPDATE pages SET fetched_at = now() - interval '90 days'")
    result = pipeline.purge()
    assert (
        result["pages_purged"] > 0 and pipeline.store.scalar("SELECT count(*) FROM pages WHERE text IS NOT NULL") == 0
    )
    assert pipeline.profile(int(draft["account_id"])).facts, "cited facts outlive the raw page text"


@pytest.mark.parametrize(
    ("body", "label", "check"),
    [
        ("Please take me off your list.", "unsubscribe", "suppressed"),
        ("Sure, let's talk. Does Thursday work for a call?", "meeting_request", "meeting"),
        ("Not a priority this quarter. Reach out again in January.", "not_now", "paused"),
        ("We already use TrackRight and we're happy with it.", "objection", "cancelled"),
        ("I am out of the office until next week.", "out_of_office", "moved"),
    ],
)
def test_reply_routing(pipeline: Scout, body: str, label: str, check: str) -> None:
    draft = _pending(pipeline)
    pipeline.approve(int(draft["id"]), reviewer="ivan")
    pipeline.send_due(pipeline.now() + dt.timedelta(days=2))
    email = draft["contact"]["email"]
    reply_id = pipeline.ingest_reply(from_email=email, subject="Re: hello", body=body)
    result = pipeline.classify(reply_id)
    assert result.label == label
    statuses = {
        r["status"]
        for r in pipeline.store.all("SELECT status FROM messages WHERE draft_id = %s AND step > 1", (draft["id"],))
    }
    if check == "suppressed":
        assert pipeline.store.suppressed(email) and statuses == {"cancelled"}
    elif check == "meeting":
        assert pipeline.store.scalar("SELECT count(*) FROM meetings") == 1
        assert pipeline.store.scalar("SELECT count(*) FROM mock_crm WHERE object_type = 'deals'") == 1
        assert statuses == {"cancelled"}
    elif check == "paused":
        assert statuses == {"paused"}
    elif check == "cancelled":
        assert statuses == {"cancelled"}
    elif check == "moved":
        assert statuses <= {"scheduled", "sent"}
    actions = pipeline.store.scalar("SELECT actions FROM replies WHERE id = %s", (reply_id,))
    assert actions, "every routing decision is recorded"


def test_simulated_replies_and_crm_sync(pipeline: Scout) -> None:
    for d in pipeline.store.all(
        "SELECT DISTINCT ON (account_id) id FROM drafts WHERE status = 'pending' ORDER BY account_id, id LIMIT 6"
    ):
        pipeline.approve(int(d["id"]), reviewer="ivan")
    pipeline.send_due(pipeline.now() + dt.timedelta(days=2))
    ids = pipeline.simulate_replies()
    assert len(ids) == 6 and pipeline.process_inbox() == 6
    assert pipeline.store.scalar("SELECT count(*) FROM replies WHERE label IS NULL") == 0
    crm = {
        r["object_type"]: r["n"]
        for r in pipeline.store.all("SELECT object_type, count(*) AS n FROM mock_crm GROUP BY 1")
    }
    assert crm["companies"] == 6 and crm["contacts"] == 6 and crm["emails"] >= 12
    accounts_csv = pipeline.export_csv("accounts")
    assert accounts_csv.splitlines()[0].startswith("domain,name,route,score") and len(accounts_csv.splitlines()) == 61
    assert "reply" in pipeline.export_csv("activities")
    with pytest.raises(ScoutError):
        pipeline.export_csv("passwords")


def test_capture_only_mailer() -> None:
    with pytest.raises(CaptureOnlyError):
        SmtpMailer("smtp.gmail.com", 587)
    SmtpMailer("mailpit", 1025)


def test_audit_log_covers_decisions(pipeline: Scout) -> None:
    draft = _pending(pipeline)
    pipeline.approve(int(draft["id"]), reviewer="ivan")
    pipeline.reject(int(_pending(pipeline)["id"]), reviewer="ivan", reason="tone")
    actions = {r["action"] for r in pipeline.store.all("SELECT DISTINCT action FROM audit_log")}
    assert {
        "seed.demo",
        "account.researched",
        "account.qualified",
        "draft.created",
        "draft.approved",
        "draft.rejected",
        "injection.quarantined",
    } <= actions


def test_job_queue(pipeline: Scout) -> None:
    from scout.jobs import run_job

    job_id = pipeline.store.enqueue("qualify", {"account_id": 1})
    job = pipeline.store.claim_job()
    assert job is not None and job["id"] == job_id and pipeline.store.claim_job() is None
    pipeline.store.finish_job(job_id, result=run_job(pipeline, job))
    assert pipeline.store.scalar("SELECT status FROM jobs WHERE id = %s", (job_id,)) == "done"
