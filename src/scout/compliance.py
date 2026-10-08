"""Compliance controls, honoured everywhere a message can be scheduled or sent:

* human approval: a message row is created only from an approved draft, and the database refuses status 'sent'
  without an approver (CHECK constraint); the sender re-checks before every send;
* suppression list (addresses and whole domains): checked when a sequence is planned, by a database trigger on
  insert and update, and again right before each send; an unsubscribe cancels every scheduled message to that
  address in every sequence;
* do-not-contact flag per account;
* daily and per-domain send caps (excess messages are deferred to the next business slot, not dropped);
* an unsubscribe link and the sender's postal address in every email (mailer.compose);
* data retention: raw page text, reply bodies and rejected drafts are purged after configurable periods;
* an audit log of every decision (research, qualification, approval, edits, sends, blocks, unsubscribes, purges).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from scout.store.db import Store


@dataclass(frozen=True)
class Limits:
    daily_send_cap: int
    per_domain_daily_cap: int
    retention_days_pages: int
    retention_days_replies: int
    retention_days_rejected_drafts: int


@dataclass(frozen=True)
class GateResult:
    ok: bool
    action: str = "send"
    """send | block | defer"""
    reason: str = ""


def send_gate(store: Store, msg: dict[str, Any], limits: Limits, now: dt.datetime) -> GateResult:
    if not msg["approved"] or not msg.get("approved_by"):
        return GateResult(False, "block", "not approved by a human")
    draft = store.one("SELECT status FROM drafts WHERE id = %s", (msg["draft_id"],))
    if draft is None or draft["status"] != "approved":
        return GateResult(False, "block", "the draft is no longer approved")
    hit = store.suppressed(msg["to_email"])
    if hit:
        return GateResult(False, "block", f"suppressed {hit['kind']} ({hit['reason']})")
    account = store.one("SELECT do_not_contact FROM accounts WHERE id = %s", (msg["account_id"],))
    if account and account["do_not_contact"]:
        return GateResult(False, "block", "account marked do-not-contact")
    day_start = now.astimezone(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = int(
        store.scalar("SELECT count(*) FROM messages WHERE status = 'sent' AND sent_at >= %s", (day_start,)) or 0
    )
    if sent_today >= limits.daily_send_cap:
        return GateResult(False, "defer", f"daily cap of {limits.daily_send_cap} reached")
    domain = msg["to_email"].split("@")[-1].lower()
    domain_today = int(
        store.scalar(
            "SELECT count(*) FROM messages WHERE status = 'sent' AND sent_at >= %s AND split_part(lower(to_email), '@', 2) = %s",
            (day_start, domain),
        )
        or 0
    )
    if domain_today >= limits.per_domain_daily_cap:
        return GateResult(False, "defer", f"per-domain cap of {limits.per_domain_daily_cap} for {domain} reached")
    return GateResult(True)


def cancel_for_email(store: Store, email: str, reason: str) -> int:
    return store.run(
        "UPDATE messages SET status = 'cancelled', status_reason = %s WHERE lower(to_email) = %s AND status IN ('scheduled', 'deferred', 'paused')",
        (reason, email.lower()),
    )


def cancel_for_domain(store: Store, domain: str, reason: str) -> int:
    return store.run(
        "UPDATE messages SET status = 'cancelled', status_reason = %s "
        "WHERE split_part(lower(to_email), '@', 2) = %s AND status IN ('scheduled', 'deferred', 'paused')",
        (reason, domain.lower()),
    )


def stop_sequence(store: Store, draft_id: int, reason: str) -> int:
    return store.run(
        "UPDATE messages SET status = 'cancelled', status_reason = %s WHERE draft_id = %s AND status IN ('scheduled', 'deferred', 'paused')",
        (reason, draft_id),
    )


def suppress_email(store: Store, email: str, *, reason: str, source: str, actor: str) -> int:
    added = store.suppress(email, kind="email", reason=reason, source=source)
    cancelled = cancel_for_email(store, email, f"suppressed: {reason}")
    store.audit(
        actor,
        "suppression.add",
        "email",
        email.lower(),
        {"reason": reason, "source": source, "new": added, "cancelled": cancelled},
    )
    return cancelled


def suppress_domain(store: Store, domain: str, *, reason: str, source: str, actor: str) -> int:
    added = store.suppress(domain, kind="domain", reason=reason, source=source)
    cancelled = cancel_for_domain(store, domain, f"suppressed domain: {reason}")
    store.audit(
        actor,
        "suppression.add",
        "domain",
        domain.lower(),
        {"reason": reason, "source": source, "new": added, "cancelled": cancelled},
    )
    return cancelled


def unsubscribe(store: Store, token: str, *, source: str = "link") -> dict[str, Any] | None:
    msg = store.one("SELECT id, to_email, account_id FROM messages WHERE unsubscribe_token = %s", (token,))
    if msg is None:
        return None
    cancelled = suppress_email(store, msg["to_email"], reason="unsubscribed", source=source, actor="recipient")
    return {"email": msg["to_email"], "cancelled": cancelled}


def purge(store: Store, limits: Limits, now: dt.datetime) -> dict[str, int]:
    pages = store.run(
        "UPDATE pages SET text = NULL, purged_at = %s WHERE purged_at IS NULL AND fetched_at < %s",
        (now, now - dt.timedelta(days=limits.retention_days_pages)),
    )
    replies = store.run(
        "UPDATE replies SET body = NULL, purged_at = %s WHERE purged_at IS NULL AND received_at < %s",
        (now, now - dt.timedelta(days=limits.retention_days_replies)),
    )
    drafts = store.run(
        "DELETE FROM drafts WHERE status = 'rejected' AND reviewed_at < %s",
        (now - dt.timedelta(days=limits.retention_days_rejected_drafts),),
    )
    result = {"pages_purged": pages, "reply_bodies_purged": replies, "rejected_drafts_deleted": drafts}
    store.audit("system", "retention.purge", "data", None, result)
    return result
