"""Scripted compliance checks against a real PostgreSQL database (a separate `<db>_compliance` database that is
reset first) with the offline model and an in-memory mailer. No model calls, no network."""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg

from scout import compliance
from scout.config import Settings
from scout.mailer import CaptureOnlyError, MemoryMailer, SmtpMailer
from scout.services import Scout, ScoutError
from scout.store.db import Store


def _compliance_url(url: str) -> str:
    parts = urlsplit(url)
    name = parts.path.lstrip("/") + "_compliance"
    admin = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    with psycopg.connect(admin, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            conn.execute(f'CREATE DATABASE "{name}"')
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, ""))


def run_compliance(settings: Settings) -> dict[str, Any]:
    url = _compliance_url(settings.database_url)
    s = settings.model_copy(
        update={"database_url": url, "llm_provider": "fake", "llm_ledger": None, "crm_mode": "mock"}
    )
    store = Store(url)
    store.reset()
    mailer = MemoryMailer()
    scout = Scout(s, store, mailer=mailer)
    scout.seed_demo()
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    qualified = []
    for row in store.all("SELECT id, domain FROM accounts ORDER BY id"):
        out = scout.run_pipeline(int(row["id"]))
        if out.get("drafts"):
            qualified.append(int(row["id"]))
        if len(qualified) >= 8:
            break
    now = scout.now()
    far = now + dt.timedelta(days=30)

    # 1. Nothing is sent from a draft nobody approved.
    pending = store.one("SELECT * FROM drafts WHERE status = 'pending' ORDER BY id LIMIT 1")
    assert pending is not None
    store.run(
        "INSERT INTO messages (draft_id, account_id, step, to_email, to_name, subject, body, timezone, scheduled_at, approved, unsubscribe_token) "
        "VALUES (%s, %s, 99, 'sneaky@example.org', 'Sneaky', 's', 'b', 'UTC', %s, false, 'tok-unapproved')",
        (pending["id"], pending["account_id"], now - dt.timedelta(minutes=5)),
    )
    result = scout.send_due(now)
    status = store.scalar("SELECT status FROM messages WHERE unsubscribe_token = 'tok-unapproved'")
    check(
        "An unapproved message is not sent (send gate)", status == "blocked" and result["sent"] == 0, f"status={status}"
    )
    try:
        store.run("UPDATE messages SET status = 'sent' WHERE unsubscribe_token = 'tok-unapproved'")
        check("The database refuses status 'sent' without an approver", False, "update succeeded")
    except psycopg.errors.CheckViolation:
        check("The database refuses status 'sent' without an approver", True, "CHECK sent_requires_approval")
    check(
        "Pending drafts have no outgoing messages",
        int(
            store.scalar(
                "SELECT count(*) FROM messages m JOIN drafts d ON d.id = m.draft_id WHERE d.status = 'pending' AND m.approved"
            )
            or 0
        )
        == 0,
    )

    # 2. Approve, send with fast-forward, check every captured email.
    first = qualified[0]
    draft_a = store.one(
        "SELECT id FROM drafts WHERE account_id = %s AND status = 'pending' ORDER BY variant LIMIT 1", (first,)
    )
    assert draft_a is not None
    scout.approve(int(draft_a["id"]), reviewer="compliance-check")
    contact_email = store.scalar(
        "SELECT to_email FROM messages WHERE draft_id = %s AND step = 1 AND approved", (draft_a["id"],)
    )
    # A second sequence to the same person (re-drafted and approved again).
    scout.draft(first)
    draft_b = store.one(
        "SELECT id FROM drafts WHERE account_id = %s AND status = 'pending' ORDER BY variant LIMIT 1", (first,)
    )
    assert draft_b is not None
    scout.approve(int(draft_b["id"]), reviewer="compliance-check")
    sequences = int(
        store.scalar("SELECT count(DISTINCT draft_id) FROM messages WHERE to_email = %s AND approved", (contact_email,))
        or 0
    )
    token = store.scalar(
        "SELECT unsubscribe_token FROM messages WHERE draft_id = %s AND step = 1 AND approved", (draft_a["id"],)
    )
    scout.send_due(far)
    sent_before = int(
        store.scalar("SELECT count(*) FROM messages WHERE to_email = %s AND status = 'sent'", (contact_email,)) or 0
    )
    compliance.unsubscribe(store, str(token))
    remaining = int(
        store.scalar(
            "SELECT count(*) FROM messages WHERE to_email = %s AND status IN ('scheduled', 'deferred')",
            (contact_email,),
        )
        or 0
    )
    scout.send_due(far + dt.timedelta(days=30))
    sent_after = int(
        store.scalar("SELECT count(*) FROM messages WHERE to_email = %s AND status = 'sent'", (contact_email,)) or 0
    )
    check(
        "Unsubscribing from one sequence stops every sequence to that address",
        sequences == 2 and remaining == 0 and sent_after == sent_before,
        f"{sequences} sequences, {remaining} still scheduled, sent {sent_before} -> {sent_after}",
    )

    # 3. Database trigger: a message to a suppressed address cannot be scheduled.
    store.run(
        "INSERT INTO messages (draft_id, account_id, step, to_email, to_name, subject, body, timezone, scheduled_at, approved, approved_by, unsubscribe_token) "
        "VALUES (%s, %s, 9, %s, 'X', 's', 'b', 'UTC', %s, true, 'test', 'tok-trigger')",
        (draft_a["id"], first, contact_email, far),
    )
    check(
        "A suppressed address cannot be scheduled (database trigger)",
        store.scalar("SELECT status FROM messages WHERE unsubscribe_token = 'tok-trigger'") == "blocked",
    )
    # Re-drafting for a suppressed contact is refused.
    try:
        scout.draft(first)
        check("Drafting for a suppressed contact is refused", False)
    except ScoutError as exc:
        check("Drafting for a suppressed contact is refused", "suppressed" in str(exc), str(exc))

    # 4. Reply-based opt-out.
    second = qualified[1]
    draft_c = store.one(
        "SELECT id FROM drafts WHERE account_id = %s AND status = 'pending' ORDER BY variant LIMIT 1", (second,)
    )
    assert draft_c is not None
    scout.approve(int(draft_c["id"]), reviewer="compliance-check")
    scout.send_due(scout.now() + dt.timedelta(days=4))
    email_c = store.scalar(
        "SELECT to_email FROM messages WHERE draft_id = %s AND step = 1 AND approved", (draft_c["id"],)
    )
    reply_id = scout.ingest_reply(
        from_email=str(email_c), subject="Re: hello", body="Please take me off your list, thanks."
    )
    scout.classify(reply_id)
    check(
        "An opt-out reply suppresses the address and cancels its follow-ups",
        bool(store.suppressed(str(email_c)))
        and int(
            store.scalar(
                "SELECT count(*) FROM messages WHERE to_email = %s AND status IN ('scheduled', 'deferred')", (email_c,)
            )
            or 0
        )
        == 0,
    )

    # 5. Do-not-contact.
    third = qualified[2]
    draft_d = store.one(
        "SELECT id FROM drafts WHERE account_id = %s AND status = 'pending' ORDER BY variant LIMIT 1", (third,)
    )
    assert draft_d is not None
    scout.approve(int(draft_d["id"]), reviewer="compliance-check")
    cancelled = scout.set_do_not_contact(third, True, actor="compliance-check")
    sent_dnc = scout.send_due(far)
    check(
        "Do-not-contact cancels scheduled messages",
        cancelled >= 1
        and int(store.scalar("SELECT count(*) FROM messages WHERE account_id = %s AND status = 'sent'", (third,)) or 0)
        == 0,
        f"cancelled {cancelled}, {sent_dnc}",
    )
    try:
        scout.draft(third)
        check("Drafting for a do-not-contact account is refused", False)
    except ScoutError:
        check("Drafting for a do-not-contact account is refused", True)

    # 6. Caps: daily 2, per domain 1 (a re-drafted second sequence to the same domain on the same day).
    # Earlier sends are moved a year back so the caps start from zero.
    store.run("UPDATE messages SET sent_at = sent_at - interval '365 days' WHERE status = 'sent'")
    store.set_setting("compliance", {"daily_send_cap": 2, "per_domain_daily_cap": 1})
    capped: list[int] = []
    for account_id in qualified[3:7]:
        d = store.one(
            "SELECT id FROM drafts WHERE account_id = %s AND status = 'pending' ORDER BY variant LIMIT 1", (account_id,)
        )
        if d:
            scout.approve(int(d["id"]), reviewer="compliance-check")
            capped.append(int(d["id"]))
    # Same domain twice: re-draft and approve one account again.
    scout.draft(qualified[3])
    again = store.one(
        "SELECT id FROM drafts WHERE account_id = %s AND status = 'pending' ORDER BY variant LIMIT 1", (qualified[3],)
    )
    if again:
        scout.approve(int(again["id"]), reviewer="compliance-check")
        capped.append(int(again["id"]))
    for _ in range(60):  # deferred messages move to the next day; keep sending until everything is out
        result = scout.send_due(far + dt.timedelta(days=60))
        if not result["sent"] and not result["deferred"]:
            break
    recent = now - dt.timedelta(days=200)
    per_day = store.all(
        "SELECT date_trunc('day', sent_at) AS day, count(*) AS n FROM messages WHERE status = 'sent' AND sent_at > %s GROUP BY 1",
        (recent,),
    )
    per_domain = store.all(
        "SELECT date_trunc('day', sent_at) AS day, split_part(to_email, '@', 2) AS domain, count(*) AS n FROM messages WHERE status = 'sent' AND sent_at > %s GROUP BY 1, 2",
        (recent,),
    )
    unsent = int(
        store.scalar(
            "SELECT count(*) FROM messages WHERE draft_id = ANY(%s) AND status IN ('scheduled', 'deferred')", (capped,)
        )
        or 0
    )
    deferred_events = int(store.scalar("SELECT count(*) FROM audit_log WHERE action = 'message.deferred'") or 0)
    check(
        "Daily and per-domain caps are never exceeded (excess is deferred)",
        bool(per_day)
        and all(r["n"] <= 2 for r in per_day)
        and all(r["n"] <= 1 for r in per_domain)
        and deferred_events > 0
        and unsent == 0,
        f"max/day {max((r['n'] for r in per_day), default=0)}, max/domain/day {max((r['n'] for r in per_domain), default=0)}, deferrals {deferred_events}, still waiting {unsent}",
    )
    store.set_setting("compliance", {})

    # 7. Every captured email carries the unsubscribe link, the headers and the postal address.
    postal = scout.config.seller.postal_address
    ok = [
        m
        for m in mailer.sent
        if "/u/" in m.get_content()
        and m["List-Unsubscribe"]
        and m["List-Unsubscribe-Post"]
        and postal in m.get_content()
    ]
    check(
        "Every sent email has an unsubscribe link, RFC 8058 headers and the postal address",
        len(mailer.sent) > 0 and len(ok) == len(mailer.sent),
        f"{len(ok)}/{len(mailer.sent)}",
    )

    # 8. Capture-only mailer.
    try:
        SmtpMailer("smtp.gmail.com", 587)
        check("The mailer refuses a real SMTP server in capture-only mode", False)
    except CaptureOnlyError:
        check("The mailer refuses a real SMTP server in capture-only mode", True)

    # 9. Retention.
    store.run("UPDATE pages SET fetched_at = now() - interval '45 days' WHERE id IN (SELECT id FROM pages LIMIT 10)")
    store.run("UPDATE replies SET received_at = now() - interval '400 days'")
    purged = scout.purge()
    check(
        "Retention purges old page text and reply bodies",
        purged["pages_purged"] >= 10
        and purged["reply_bodies_purged"] >= 1
        and int(store.scalar("SELECT count(*) FROM replies WHERE body IS NOT NULL") or 0) == 0,
        str(purged),
    )

    # 10. Audit trail.
    actions = {r["action"] for r in store.all("SELECT DISTINCT action FROM audit_log")}
    needed = {
        "draft.approved",
        "message.sent",
        "message.blocked",
        "suppression.add",
        "account.do_not_contact",
        "retention.purge",
        "reply.classified",
        "account.researched",
        "account.qualified",
    }
    check("Every decision is in the audit log", needed <= actions, f"missing: {sorted(needed - actions)}")
    store.close()
    return {
        "checks": checks,
        "passed": sum(c["passed"] for c in checks),
        "total": len(checks),
        "emails_captured": len(mailer.sent),
    }
