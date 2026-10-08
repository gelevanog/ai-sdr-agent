"""The application service: one object that owns the settings, the database, the models, the crawler, the mailer
and the CRM adapter, and implements every pipeline step. The API, the worker and the CLI all call into it."""

from __future__ import annotations

import csv
import datetime as dt
import difflib
import hashlib
import io
import time
from functools import cached_property
from pathlib import Path
from typing import Any

from scout import compliance
from scout.config import Settings
from scout.crm.hubspot import CRMError, HubSpotClient, MockHubSpot
from scout.icp import Config, load_config
from scout.llm.base import ChatModel
from scout.llm.factory import model_for
from scout.logging_config import get_logger
from scout.mailer import Mailer, Outgoing, SmtpMailer, compose, new_token
from scout.models import CompanyProfile, Contact, DraftVariant, Email, Qualification, ReplyClassification
from scout.outreach.checker import build_evidence, deterministic_check
from scout.outreach.draft import draft_account
from scout.outreach.schedule import next_business_slot, plan_sequence
from scout.outreach.timezones import guess_timezone
from scout.qualify.contacts import ClientContact, select_contact
from scout.qualify.judge import apply_judgment
from scout.qualify.rubric import score_rules
from scout.replies.classify import ReplyCase, classify_reply, load_replies
from scout.replies.meetings import find_slot
from scout.research.agent import ResearchResult, research_account
from scout.research.crawler import Crawler, Fetcher, FileFetcher, HttpFetcher, RateLimiter
from scout.store.db import Store
from psycopg.types.json import Jsonb
from scout.synthetic.generator import write_web
from scout.synthetic.spec import load_specs

log = get_logger(__name__)
SYSTEM = "system"


class ScoutError(RuntimeError):
    pass


def _diff_stats(before: str, after: str) -> dict[str, Any]:
    matcher = difflib.SequenceMatcher(None, before, after)
    added = sum(j2 - j1 for tag, _, _, j1, j2 in matcher.get_opcodes() if tag in {"insert", "replace"})
    removed = sum(i2 - i1 for tag, i1, i2, _, _ in matcher.get_opcodes() if tag in {"delete", "replace"})
    return {"similarity": round(matcher.ratio(), 3), "chars_added": added, "chars_removed": removed}


class Scout:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        *,
        mailer: Mailer | None = None,
        fetcher: Fetcher | None = None,
        model: ChatModel | None = None,
        verifier: ChatModel | None = None,
        crm_transport: Any = None,
        now: Any = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._mailer = mailer
        self._fetcher = fetcher
        self._model = model
        self._verifier = verifier
        self._crm_transport = crm_transport
        self._now = now or (lambda: dt.datetime.now(dt.UTC))

    # ------------------------------------------------------------------ wiring
    def now(self) -> dt.datetime:
        value: dt.datetime = self._now()
        return value

    @property
    def config(self) -> Config:
        override = self.store.get_setting("icp_yaml")
        if override:
            import yaml

            return Config.model_validate(yaml.safe_load(override))
        return load_config(self.settings.icp_file)

    @property
    def limits(self) -> compliance.Limits:
        s = self.settings
        overrides = self.store.get_setting("compliance") or {}
        values = {
            "daily_send_cap": s.daily_send_cap,
            "per_domain_daily_cap": s.per_domain_daily_cap,
            "retention_days_pages": s.retention_days_pages,
            "retention_days_replies": s.retention_days_replies,
            "retention_days_rejected_drafts": s.retention_days_rejected_drafts,
        }
        values.update({k: int(v) for k, v in overrides.items() if k in values})
        return compliance.Limits(**values)

    def model(self, tag: str) -> ChatModel:
        if self._model is not None:
            return self._model
        return model_for(self.settings, tag=tag)

    def verifier(self) -> ChatModel:
        if self._verifier is not None:
            return self._verifier
        return self.model("verify_claims")

    @property
    def mailer(self) -> Mailer:
        if self._mailer is None:
            self._mailer = SmtpMailer(self.settings.smtp_host, self.settings.smtp_port, capture_only=self.settings.smtp_capture_only)
        return self._mailer

    def fetcher(self, live: bool) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        s = self.settings
        if live:
            return HttpFetcher(user_agent=s.crawl_user_agent, timeout_seconds=s.crawl_timeout_seconds)
        if s.synthetic_web_url:
            return HttpFetcher(user_agent=s.crawl_user_agent, timeout_seconds=s.crawl_timeout_seconds, base_url=s.synthetic_web_url)
        return FileFetcher(s.synthetic_web_dir)

    def crawler(self, live: bool) -> Crawler:
        limiter = RateLimiter(self.settings.crawl_min_seconds_per_domain if live else 0)
        return Crawler(self.fetcher(live), user_agent=self.settings.crawl_user_agent, max_pages=self.settings.crawl_max_pages, limiter=limiter, live=live)

    # ------------------------------------------------------------------ seeding and targets
    def ensure_synthetic_web(self, *, force: bool = False) -> int:
        manifest = self.settings.synthetic_web_dir / "manifest.json"
        if manifest.exists() and not force:
            return 0
        specs = load_specs(self.settings.companies_file)
        files = write_web(specs, self.settings.synthetic_web_dir, self.settings.demo_seed)
        return len(files)

    def seed_demo(self, *, force: bool = False) -> int:
        self.store.migrate()
        self.ensure_synthetic_web()
        if not force and int(self.store.scalar("SELECT count(*) FROM accounts") or 0) > 0:
            return 0
        added = 0
        for spec in load_specs(self.settings.companies_file):
            added += self.add_account(spec.url, spec.name, source="synthetic", actor=SYSTEM) is not None
        self.store.audit(SYSTEM, "seed.demo", "accounts", None, {"accounts": added})
        return added

    def add_account(self, url: str, name: str | None = None, *, source: str = "live", actor: str = "user") -> int | None:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        domain = url.split("//", 1)[1].split("/", 1)[0].lower()
        if domain.startswith("www."):
            domain = domain[4:]
        row = self.store.one(
            "INSERT INTO accounts (domain, url, name, source) VALUES (%s, %s, %s, %s) ON CONFLICT (domain) DO NOTHING RETURNING id",
            (domain, url if url.endswith("/") else url + "/", name or domain, source),
        )
        if row:
            self.store.audit(actor, "account.add", "account", row["id"], {"domain": domain, "source": source})
            return int(row["id"])
        return None

    def import_client_contacts(self, rows: list[dict[str, str]], *, actor: str = "user") -> int:
        """Live mode: business-role contacts the client supplies (domain, name, title, email)."""
        stored = self.store.get_setting("client_contacts") or []
        known = {(c["domain"], c["email"]) for c in stored}
        for row in rows:
            entry = {k: row.get(k, "").strip() for k in ("domain", "name", "title", "email")}
            entry["email"] = entry["email"].lower()
            if all(entry.values()) and (entry["domain"], entry["email"]) not in known:
                stored.append(entry)
                known.add((entry["domain"], entry["email"]))
        self.store.set_setting("client_contacts", stored)
        self.store.audit(actor, "contacts.import", "client_contacts", None, {"total": len(stored)})
        return len(stored)

    def account(self, account_id: int) -> dict[str, Any]:
        row = self.store.one("SELECT * FROM accounts WHERE id = %s", (account_id,))
        if row is None:
            raise ScoutError(f"account {account_id} not found")
        return row

    # ------------------------------------------------------------------ research
    def research(self, account_id: int, *, guard: bool | None = None) -> ResearchResult:
        acc = self.account(account_id)
        live = acc["source"] == "live"
        model = self.model("extract")
        result = research_account(
            acc["url"],
            crawler=self.crawler(live),
            model=model,
            config=self.config,
            today=self.settings.research_today(),
            guard=self.settings.injection_guard if guard is None else guard,
            live=live,
            max_tokens=self.settings.llm_max_tokens,
        )
        profile = result.profile
        if acc["name"] and (profile.name == profile.domain):
            profile.name = acc["name"]
        with self.store.tx() as conn:
            for page in result.crawl.pages:
                conn.execute(
                    "INSERT INTO pages (account_id, url, fetched_at, text, purged_at) VALUES (%s, %s, %s, %s, NULL) "
                    "ON CONFLICT (account_id, url) DO UPDATE SET fetched_at = EXCLUDED.fetched_at, text = EXCLUDED.text, purged_at = NULL",
                    (account_id, page.url, page.fetched_at, page.parsed.text),
                )
        self.store.run(
            "UPDATE accounts SET profile = %s, name = %s, status = %s, error = %s, research_calls = %s, research_seconds = %s, updated_at = now() WHERE id = %s",
            (
                profile.model_dump(mode="json"),
                profile.name,
                "research_failed" if result.error else "researched",
                result.error,
                profile.llm_calls,
                profile.seconds,
                account_id,
            ),
        )
        self.store.audit(
            SYSTEM,
            "account.researched",
            "account",
            account_id,
            {
                "pages": len(profile.pages),
                "facts": len(profile.facts),
                "signals": len(profile.signals),
                "rejected": len(profile.rejected),
                "injection_findings": [f.model_dump(mode="json") for f in profile.injection_findings],
                "robots_skipped": [u for u, r in profile.skipped if r == "robots.txt"],
                "model": profile.model,
                "error": result.error,
            },
        )
        for finding in profile.injection_findings:
            self.store.audit(SYSTEM, "injection.quarantined", "page", finding.url, {"rules": finding.rules, "score": finding.score, "hidden": finding.hidden, "text": finding.text[:300]})
        return result

    def profile(self, account_id: int) -> CompanyProfile:
        acc = self.account(account_id)
        if not acc["profile"]:
            raise ScoutError(f"account {account_id} has not been researched")
        return CompanyProfile.model_validate(acc["profile"])

    # ------------------------------------------------------------------ qualification
    def qualify(self, account_id: int) -> Qualification:
        acc = self.account(account_id)
        profile = self.profile(account_id)
        config = self.config
        qualification = score_rules(profile, config, do_not_contact=bool(acc["do_not_contact"]))
        calls = 0
        if self.settings.llm_judgment:
            qualification, calls = apply_judgment(profile, qualification, config, self.model("judge"))
        client = [ClientContact(**c) for c in (self.store.get_setting("client_contacts") or [])]
        selection = select_contact(profile, config, suppressed=self.store.suppressed_emails(), client_contacts=client or None)
        country = profile.fact("country")
        hq = profile.fact("hq")
        tz = guess_timezone(hq.value if hq else None, country.value if country else None)
        self.store.run(
            "UPDATE accounts SET qualification = %s, route = %s, score = %s, status = %s, contact = %s, contact_notes = %s, timezone = %s, updated_at = now() WHERE id = %s",
            (
                qualification.model_dump(mode="json"),
                qualification.route,
                qualification.score,
                qualification.route,
                selection.contact.model_dump(mode="json") if selection.contact else None,
                Jsonb(selection.notes),
                tz,
                account_id,
            ),
        )
        self.store.audit(
            SYSTEM,
            "account.qualified",
            "account",
            account_id,
            {
                "route": qualification.route,
                "score": qualification.score,
                "rules_score": qualification.rules_score,
                "llm_adjustment": qualification.llm_adjustment,
                "disqualifiers": qualification.disqualifiers,
                "contact": selection.contact.email if selection.contact else None,
                "model_calls": calls,
            },
        )
        return qualification

    # ------------------------------------------------------------------ drafting
    def draft(self, account_id: int, *, checker: bool | None = None) -> list[int]:
        acc = self.account(account_id)
        if acc["route"] != "qualified":
            raise ScoutError(f"account {account_id} is not qualified (route: {acc['route']})")
        if acc["do_not_contact"]:
            raise ScoutError("account is marked do-not-contact")
        if not acc["contact"]:
            raise ScoutError("no contact with a published or supplied email address; add one before drafting")
        profile = self.profile(account_id)
        qualification = Qualification.model_validate(acc["qualification"])
        contact = Contact.model_validate(acc["contact"])
        if self.store.suppressed(contact.email or ""):
            raise ScoutError(f"{contact.email} is suppressed")
        use_checker = self.settings.claim_checker if checker is None else checker
        model = self.model("draft")
        result = draft_account(
            profile,
            qualification,
            contact,
            self.config,
            model=model,
            verifier=self.verifier() if use_checker else None,
            today=self.settings.research_today(),
            variants=self.settings.draft_variants,
            checker=use_checker,
            max_regenerations=self.settings.max_regenerations,
        )
        if result.error and not result.variants:
            self.store.run("UPDATE accounts SET status = 'draft_failed', error = %s WHERE id = %s", (result.error, account_id))
            self.store.audit(SYSTEM, "draft.failed", "account", account_id, {"error": result.error})
            raise ScoutError(result.error)
        self.store.run("UPDATE drafts SET status = 'superseded' WHERE account_id = %s AND status = 'pending'", (account_id,))
        ids = []
        firsts = {v.variant: v for v in result.first_drafts}
        for variant in result.variants:
            row = self.store.one(
                "INSERT INTO drafts (account_id, variant, data, first_draft, contact, model, calls) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    account_id,
                    variant.variant,
                    variant.model_dump(mode="json"),
                    firsts[variant.variant].model_dump(mode="json") if variant.variant in firsts else None,
                    contact.model_dump(mode="json"),
                    model.label,
                    result.calls,
                ),
            )
            assert row is not None
            ids.append(int(row["id"]))
        self.store.run("UPDATE accounts SET status = 'drafted', updated_at = now() WHERE id = %s", (account_id,))
        self.store.audit(
            SYSTEM,
            "draft.created",
            "account",
            account_id,
            {
                "drafts": ids,
                "model_calls": result.calls,
                "attempts": {v.variant: v.attempts for v in result.variants},
                "passed": {v.variant: bool(v.report and v.report.passed) for v in result.variants},
                "checker": use_checker,
            },
        )
        return ids

    def run_pipeline(self, account_id: int) -> dict[str, Any]:
        out: dict[str, Any] = {"account_id": account_id}
        started = time.monotonic()
        research = self.research(account_id)
        out["research_error"] = research.error
        qualification = self.qualify(account_id)
        out["route"] = qualification.route
        out["score"] = qualification.score
        acc = self.account(account_id)
        if qualification.route == "qualified" and acc["contact"]:
            try:
                out["drafts"] = self.draft(account_id)
            except ScoutError as exc:
                out["draft_error"] = str(exc)
        out["seconds"] = round(time.monotonic() - started, 2)
        return out

    # ------------------------------------------------------------------ review
    def approve(self, draft_id: int, *, reviewer: str, edits: list[dict[str, Any]] | None = None, note: str = "") -> dict[str, Any]:
        draft = self.store.one("SELECT * FROM drafts WHERE id = %s", (draft_id,))
        if draft is None:
            raise ScoutError(f"draft {draft_id} not found")
        if draft["status"] != "pending":
            raise ScoutError(f"draft {draft_id} is {draft['status']}, not pending")
        if not reviewer.strip():
            raise ScoutError("an approval needs a reviewer name")
        acc = self.account(draft["account_id"])
        contact = Contact.model_validate(draft["contact"])
        if acc["do_not_contact"]:
            raise ScoutError("account is marked do-not-contact")
        if not contact.email:
            raise ScoutError("the contact has no email address")
        if hit := self.store.suppressed(contact.email):
            raise ScoutError(f"{contact.email} is on the suppression list ({hit['reason']})")
        variant = DraftVariant.model_validate(draft["data"])
        final = [e.model_copy() for e in variant.emails]
        diffs = []
        for edit in edits or []:
            step = int(edit.get("step", 0))
            for i, email in enumerate(final):
                if email.step != step:
                    continue
                subject = str(edit.get("subject", email.subject))
                body = str(edit.get("body", email.body))
                if subject == email.subject and body == email.body:
                    continue
                before = f"Subject: {email.subject}\n\n{email.body}"
                after = f"Subject: {subject}\n\n{body}"
                diff = "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), "draft", "approved", lineterm=""))
                stats = _diff_stats(before, after)
                self.store.run(
                    "INSERT INTO feedback (draft_id, step, before, after, diff, stats) VALUES (%s, %s, %s, %s, %s, %s)",
                    (draft_id, step, before, after, diff, stats),
                )
                diffs.append({"step": step, **stats})
                final[i] = email.model_copy(update={"subject": subject, "body": body})
        warnings: list[str] = []
        if diffs:
            # The reviewer is accountable for edited text, but Scout still points out unverifiable details they added.
            edited = variant.model_copy(update={"emails": [e.model_copy(update={"claims": []}) for e in final]})
            ev = build_evidence(self.profile(acc["id"]), contact, self.config)
            warnings = [i.message for i in deterministic_check(edited, ev, self.config) if i.kind in {"unknown_number", "unknown_name", "unknown_date"}]
        now = self.now()
        tz = acc["timezone"] or "UTC"
        s = self.settings
        times = plan_sequence(now, tz, followup_business_days=s.followup_business_days[: len(final) - 1], start_hour=s.business_hours_start, end_hour=s.business_hours_end, key=str(acc["id"]))
        with self.store.tx() as conn:
            conn.execute(
                "UPDATE drafts SET status = 'approved', reviewer = %s, reviewed_at = %s, review_note = %s, edited = %s WHERE id = %s",
                (reviewer, now, note or None, Jsonb([e.model_dump(mode="json") for e in final]) if diffs else None, draft_id),
            )
            conn.execute("UPDATE drafts SET status = 'superseded' WHERE account_id = %s AND status = 'pending' AND id <> %s", (acc["id"], draft_id))
            for email, when in zip(final, times, strict=False):
                conn.execute(
                    "INSERT INTO messages (draft_id, account_id, step, to_email, to_name, subject, body, timezone, scheduled_at, approved, approved_by, unsubscribe_token) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s)",
                    (draft_id, acc["id"], email.step, contact.email.lower(), contact.name, email.subject, email.body, tz, when, reviewer, new_token()),
                )
            conn.execute("UPDATE accounts SET status = 'in_sequence', updated_at = now() WHERE id = %s", (acc["id"],))
        self.store.audit(reviewer, "draft.approved", "draft", draft_id, {"account_id": acc["id"], "edits": diffs, "warnings": warnings, "schedule": [t.isoformat() for t in times], "timezone": tz})
        return {"draft_id": draft_id, "scheduled": [t.isoformat() for t in times], "timezone": tz, "edits": diffs, "warnings": warnings}

    def reject(self, draft_id: int, *, reviewer: str, reason: str = "") -> None:
        n = self.store.run(
            "UPDATE drafts SET status = 'rejected', reviewer = %s, reviewed_at = %s, review_note = %s WHERE id = %s AND status = 'pending'",
            (reviewer, self.now(), reason or None, draft_id),
        )
        if not n:
            raise ScoutError(f"draft {draft_id} is not pending")
        self.store.audit(reviewer, "draft.rejected", "draft", draft_id, {"reason": reason})

    # ------------------------------------------------------------------ sending
    def send_due(self, as_of: dt.datetime | None = None, *, limit: int = 200) -> dict[str, int]:
        """Sends every approved message whose time has come. With `as_of` in the future (the demo's fast-forward),
        each message is treated as sent at its scheduled time, so caps and follow-up spacing behave as they would."""
        real_now = self.now()
        as_of = as_of or real_now
        simulate = as_of > real_now
        counts = {"sent": 0, "deferred": 0, "blocked": 0, "waiting": 0}
        limits = self.limits
        config = self.config
        rows = self.store.all(
            "SELECT * FROM messages WHERE status IN ('scheduled', 'deferred') AND scheduled_at <= %s ORDER BY scheduled_at, id LIMIT %s",
            (as_of, limit),
        )
        for msg in rows:
            earlier = self.store.all("SELECT status FROM messages WHERE draft_id = %s AND step < %s", (msg["draft_id"], msg["step"]))
            if any(r["status"] in {"cancelled", "blocked"} for r in earlier):
                self.store.run("UPDATE messages SET status = 'cancelled', status_reason = 'an earlier step did not go out' WHERE id = %s", (msg["id"],))
                continue
            if any(r["status"] != "sent" for r in earlier):
                counts["waiting"] += 1
                continue
            when = msg["scheduled_at"] if simulate else real_now
            gate = compliance.send_gate(self.store, msg, limits, when)
            if gate.action == "block":
                self.store.run("UPDATE messages SET status = 'blocked', status_reason = %s WHERE id = %s", (gate.reason, msg["id"]))
                self.store.audit(SYSTEM, "message.blocked", "message", msg["id"], {"reason": gate.reason, "to": msg["to_email"]})
                counts["blocked"] += 1
                continue
            if gate.action == "defer":
                tomorrow = (when + dt.timedelta(days=1)).astimezone(dt.UTC).replace(hour=0, minute=0)
                nxt = next_business_slot(tomorrow, msg["timezone"], start_hour=self.settings.business_hours_start, end_hour=self.settings.business_hours_end, jitter_key=str(msg["id"]))
                self.store.run("UPDATE messages SET status = 'deferred', status_reason = %s, scheduled_at = %s WHERE id = %s", (gate.reason, nxt, msg["id"]))
                self.store.audit(SYSTEM, "message.deferred", "message", msg["id"], {"reason": gate.reason, "until": nxt.isoformat()})
                counts["deferred"] += 1
                continue
            domain = config.seller.sender_email.split("@")[-1]
            message_id = f"<scout-{msg['id']}-{msg['unsubscribe_token'][:10]}@{domain}>"
            previous = self.store.one("SELECT message_id FROM messages WHERE draft_id = %s AND step = 1 AND status = 'sent'", (msg["draft_id"],))
            out = Outgoing(
                to_email=msg["to_email"],
                to_name=msg["to_name"],
                subject=msg["subject"] if msg["step"] == 1 else f"Re: {self._first_subject(msg['draft_id']) or msg['subject']}",
                body=msg["body"],
                unsubscribe_url=f"{self.settings.public_url.rstrip('/')}/u/{msg['unsubscribe_token']}",
                from_address=self.settings.from_address,
                postal_address=config.seller.postal_address,
                company=config.seller.company,
                message_id=message_id,
                in_reply_to=previous["message_id"] if previous and msg["step"] > 1 else None,
            )
            self.mailer.send(compose(out, now=when))
            self.store.run(
                "UPDATE messages SET status = 'sent', sent_at = %s, message_id = %s, status_reason = NULL WHERE id = %s",
                (when, message_id, msg["id"]),
            )
            self.store.audit(SYSTEM, "message.sent", "message", msg["id"], {"to": msg["to_email"], "step": msg["step"], "simulated_time": simulate})
            counts["sent"] += 1
            self._crm_log_message(msg, when)
        return counts

    def _first_subject(self, draft_id: int) -> str | None:
        row = self.store.one("SELECT subject FROM messages WHERE draft_id = %s AND step = 1", (draft_id,))
        return row["subject"] if row else None

    # ------------------------------------------------------------------ replies
    def ingest_reply(self, *, from_email: str, subject: str, body: str, in_reply_to: str | None = None, source: str = "inbox", received_at: dt.datetime | None = None) -> int:
        msg = None
        if in_reply_to:
            msg = self.store.one("SELECT * FROM messages WHERE message_id = %s", (in_reply_to.strip(),))
        if msg is None:
            msg = self.store.one("SELECT * FROM messages WHERE lower(to_email) = %s AND status = 'sent' ORDER BY sent_at DESC LIMIT 1", (from_email.lower(),))
        row = self.store.one(
            "INSERT INTO replies (message_id, account_id, from_email, subject, body, received_at, source) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (msg["id"] if msg else None, msg["account_id"] if msg else None, from_email.lower(), subject, body, received_at or self.now(), source),
        )
        assert row is not None
        self.store.audit(SYSTEM, "reply.received", "reply", row["id"], {"from": from_email.lower(), "matched_message": msg["id"] if msg else None, "source": source})
        return int(row["id"])

    def simulate_replies(self, *, count: int | None = None, cases: list[ReplyCase] | None = None) -> list[int]:
        """Drops hand-written replies (data/replies.yaml) into the inbox for sent first emails without a reply.
        The case for each message is chosen by a hash, so a re-run picks the same ones."""
        cases = cases or load_replies(Path("data/replies.yaml"))
        rows = self.store.all(
            "SELECT m.* FROM messages m WHERE m.step = 1 AND m.status = 'sent' AND NOT EXISTS (SELECT 1 FROM replies r WHERE r.message_id = m.id) ORDER BY m.id"
        )
        ids = []
        for msg in rows[: count or len(rows)]:
            case = cases[int(hashlib.sha256(msg["to_email"].encode()).hexdigest(), 16) % len(cases)]
            sender = f"mailer-daemon@{msg['to_email'].split('@')[-1]}" if case.sender == "bounce" else msg["to_email"]
            subject = case.subject or f"Re: {msg['subject']}"
            received = (msg["sent_at"] or self.now()) + dt.timedelta(hours=3)
            ids.append(self.ingest_reply(from_email=sender, subject=subject, body=case.body, in_reply_to=msg["message_id"], source="simulator", received_at=received))
        return ids

    def classify(self, reply_id: int) -> ReplyClassification:
        reply = self.store.one("SELECT * FROM replies WHERE id = %s", (reply_id,))
        if reply is None:
            raise ScoutError(f"reply {reply_id} not found")
        result, calls = classify_reply(
            reply["subject"], reply["body"] or "", reply["from_email"], model=self.model("classify_reply"), seller=self.config.seller.company, today=self.now().date()
        )
        actions = self.route_reply(reply, result)
        self.store.run(
            "UPDATE replies SET label = %s, classification = %s, actions = %s WHERE id = %s",
            (result.label, result.model_dump(mode="json"), Jsonb(actions), reply_id),
        )
        self.store.audit(SYSTEM, "reply.classified", "reply", reply_id, {"label": result.label, "source": result.source, "objection": result.objection_type, "actions": actions, "model_calls": calls})
        return result

    def route_reply(self, reply: dict[str, Any], result: ReplyClassification) -> list[str]:
        actions: list[str] = []
        msg = self.store.one("SELECT * FROM messages WHERE id = %s", (reply["message_id"],)) if reply["message_id"] else None
        draft_id = msg["draft_id"] if msg else None
        account_id = reply["account_id"]
        contact_email = msg["to_email"] if msg else reply["from_email"]
        label = result.label
        if label == "unsubscribe":
            n = compliance.suppress_email(self.store, contact_email, reason="asked not to be contacted (reply)", source="reply", actor="recipient")
            actions.append(f"added {contact_email} to the suppression list; cancelled {n} scheduled message(s)")
        elif label == "bounce":
            n = compliance.suppress_email(self.store, contact_email, reason="hard bounce", source="bounce", actor=SYSTEM)
            actions.append(f"bounce: suppressed {contact_email}; cancelled {n} scheduled message(s)")
        elif label == "out_of_office" and draft_id:
            resume = result.resume_on or (self.now().date() + dt.timedelta(days=7))
            start = dt.datetime.combine(resume + dt.timedelta(days=1), dt.time(0, 0), dt.UTC)
            tz = msg["timezone"] if msg else "UTC"
            nxt = next_business_slot(start, tz, start_hour=self.settings.business_hours_start, end_hour=self.settings.business_hours_end)
            n = self.store.run(
                "UPDATE messages SET scheduled_at = GREATEST(scheduled_at, %s), status_reason = 'out of office' WHERE draft_id = %s AND status IN ('scheduled', 'deferred')",
                (nxt, draft_id),
            )
            actions.append(f"out of office until {resume}: moved {n} follow-up(s) to {nxt.date()} or later")
        elif draft_id:
            reason = {"not_now": "prospect asked to wait", "interested": "prospect replied (interested)", "meeting_request": "meeting requested",
                      "referral": "referred to a colleague", "objection": f"objection ({result.objection_type})"}[label]  # fmt: skip
            if label == "not_now":
                resume = result.resume_on or (self.now().date() + dt.timedelta(days=90))
                n = self.store.run("UPDATE messages SET status = 'paused', status_reason = %s WHERE draft_id = %s AND status IN ('scheduled', 'deferred')", (f"snoozed until {resume}", draft_id))
                actions.append(f"snoozed the sequence until {resume} ({n} follow-up(s) paused); a human decides whether to re-engage")
            else:
                n = compliance.stop_sequence(self.store, draft_id, reason)
                actions.append(f"stopped the sequence ({n} follow-up(s) cancelled)")
        if label == "meeting_request" and account_id:
            acc = self.account(account_id)
            seller = self.config.seller
            busy = [(r["starts_at"], r["ends_at"]) for r in self.store.all("SELECT starts_at, ends_at FROM meetings WHERE status = 'held'")]
            slot = find_slot(
                self.now(),
                seller_tz=seller.meeting_timezone,
                seller_hours=seller.meeting_hours,
                prospect_tz=acc["timezone"] or "UTC",
                minutes=seller.meeting_minutes,
                busy=busy,
            )
            if slot:
                self.store.run(
                    "INSERT INTO meetings (account_id, reply_id, contact_email, starts_at, ends_at, timezone) VALUES (%s, %s, %s, %s, %s, %s)",
                    (account_id, reply["id"], contact_email, slot[0], slot[1], acc["timezone"] or "UTC"),
                )
                actions.append(f"held a {seller.meeting_minutes}-minute slot at {slot[0].isoformat()}; the confirmation email waits for approval")
            else:
                actions.append("no free slot in the next two weeks; flagged for a human")
        if label == "referral" and result.referral_email:
            if self.store.suppressed(result.referral_email):
                actions.append(f"referral {result.referral_email} is suppressed; not added")
            else:
                actions.append(f"referral to {result.referral_email}: added as a pending contact for review (not emailed automatically)")
        if label in {"interested", "meeting_request"} and account_id:
            actions += self._crm_positive_reply(account_id, contact_email, label)
        if label == "objection":
            actions.append(f"objection ({result.objection_type}) logged for the account owner")
        if account_id:
            status = {"meeting_request": "meeting", "interested": "engaged", "unsubscribe": "opted_out", "bounce": "bounced"}.get(label, "replied")
            self.store.run("UPDATE accounts SET status = %s, updated_at = now() WHERE id = %s", (status, account_id))
            self._crm_log_reply(account_id, contact_email, reply, label)
        return actions

    def process_inbox(self) -> int:
        rows = self.store.all("SELECT id FROM replies WHERE label IS NULL ORDER BY id")
        for row in rows:
            self.classify(int(row["id"]))
        return len(rows)

    def ingest_folder(self, folder: Path) -> list[int]:
        """Replies dropped as .eml files (e.g. exported from a mailbox) into a folder."""
        from email import message_from_bytes, policy

        ids = []
        for path in sorted(folder.glob("*.eml")):
            msg = message_from_bytes(path.read_bytes(), policy=policy.default)
            body_part = msg.get_body(preferencelist=("plain",))  # type: ignore[attr-defined]
            body = body_part.get_content() if body_part else ""
            ids.append(
                self.ingest_reply(
                    from_email=str(msg.get("From", "")).split("<")[-1].rstrip(">").strip(),
                    subject=str(msg.get("Subject", "")),
                    body=body,
                    in_reply_to=str(msg.get("In-Reply-To") or "") or None,
                    source=f"folder:{path.name}",
                )
            )
            path.rename(path.with_suffix(".eml.done"))
        return ids

    # ------------------------------------------------------------------ compliance
    def set_do_not_contact(self, account_id: int, flag: bool, *, actor: str) -> int:
        self.store.run("UPDATE accounts SET do_not_contact = %s, updated_at = now() WHERE id = %s", (flag, account_id))
        cancelled = 0
        if flag:
            cancelled = self.store.run(
                "UPDATE messages SET status = 'cancelled', status_reason = 'account marked do-not-contact' WHERE account_id = %s AND status IN ('scheduled', 'deferred', 'paused')",
                (account_id,),
            )
        self.store.audit(actor, "account.do_not_contact", "account", account_id, {"flag": flag, "cancelled": cancelled})
        return cancelled

    def purge(self) -> dict[str, int]:
        return compliance.purge(self.store, self.limits, self.now())

    # ------------------------------------------------------------------ CRM
    def crm(self) -> HubSpotClient | None:
        mode = self.settings.crm_mode
        if mode == "off":
            return None
        if mode == "hubspot":
            return HubSpotClient(base_url=self.settings.hubspot_base_url, token=self.settings.hubspot_token)
        transport = self._crm_transport or self.mock_crm().transport()
        return HubSpotClient(base_url="https://mock-hubspot.local", token="mock-" + "token", transport=transport)

    def mock_crm(self) -> MockHubSpot:
        store = self.store

        def insert(object_type: str, props: dict[str, Any], associations: list[dict[str, Any]]) -> str:
            row = store.one("INSERT INTO mock_crm (object_type, properties, associations) VALUES (%s, %s, %s) RETURNING id", (object_type, props, associations))
            assert row is not None
            return str(row["id"])

        def update(object_type: str, object_id: str, props: dict[str, Any]) -> bool:
            return store.run("UPDATE mock_crm SET properties = properties || %s, updated_at = now() WHERE id = %s AND object_type = %s", (props, int(object_id), object_type)) > 0

        def find(object_type: str, prop: str, value: str) -> str | None:
            row = store.one("SELECT id FROM mock_crm WHERE object_type = %s AND lower(properties->>%s) = lower(%s) ORDER BY id LIMIT 1", (object_type, prop, value))
            return str(row["id"]) if row else None

        def associate(from_type: str, from_id: str, to_type: str, to_id: str) -> None:
            store.run("UPDATE mock_crm SET associations = associations || %s WHERE id = %s", ([{"to": {"id": to_id}, "type": to_type}], int(from_id)))

        return MockHubSpot(insert=insert, update=update, find=find, associate=associate)

    def _crm_ids(self, account_id: int, contact_email: str | None) -> tuple[HubSpotClient, str, str | None] | None:
        client = self.crm()
        if client is None:
            return None
        acc = self.account(account_id)
        profile = CompanyProfile.model_validate(acc["profile"]) if acc["profile"] else None
        props: dict[str, Any] = {"name": acc["name"], "scout_fit_score": acc["score"], "scout_route": acc["route"]}
        if profile:
            if (emp := profile.number("employees")) is not None:
                props["numberofemployees"] = emp
            if country := profile.fact("country"):
                props["country"] = country.value
            if industry := profile.industry:
                props["industry_description"] = industry
        company_id = client.upsert_company(acc["domain"], props)
        self._link("company", acc["domain"], company_id)
        contact_id = None
        if contact_email:
            contact = acc["contact"] or {}
            name = str(contact.get("name") or "")
            first, _, last = name.partition(" ")
            contact_id = client.upsert_contact(contact_email, {"firstname": first, "lastname": last, "jobtitle": contact.get("title") or ""}, company_id)
            self._link("contact", contact_email, contact_id)
        return client, company_id, contact_id

    def _link(self, object_type: str, ref: str, crm_id: str) -> None:
        self.store.run(
            "INSERT INTO crm_links (object_type, local_ref, crm_id) VALUES (%s, %s, %s) ON CONFLICT (object_type, local_ref) DO UPDATE SET crm_id = EXCLUDED.crm_id, synced_at = now()",
            (object_type, ref, crm_id),
        )

    def _crm_log_message(self, msg: dict[str, Any], when: dt.datetime) -> None:
        try:
            ids = self._crm_ids(msg["account_id"], msg["to_email"])
            if ids:
                client, company_id, contact_id = ids
                assert contact_id is not None
                email_id = client.log_email(contact_id=contact_id, company_id=company_id, subject=msg["subject"], text=msg["body"], direction="EMAIL", when=when)
                self._link("email", str(msg["id"]), email_id)
        except CRMError as exc:
            self.store.audit(SYSTEM, "crm.error", "message", msg["id"], {"error": str(exc)[:200]})

    def _crm_log_reply(self, account_id: int, contact_email: str, reply: dict[str, Any], label: str) -> None:
        try:
            ids = self._crm_ids(account_id, contact_email)
            if ids:
                client, company_id, contact_id = ids
                assert contact_id is not None
                client.log_email(contact_id=contact_id, company_id=company_id, subject=reply["subject"], text=f"[{label}] {reply['body'] or ''}", direction="INCOMING_EMAIL", when=reply["received_at"])
        except CRMError as exc:
            self.store.audit(SYSTEM, "crm.error", "reply", reply["id"], {"error": str(exc)[:200]})

    def _crm_positive_reply(self, account_id: int, contact_email: str, label: str) -> list[str]:
        try:
            ids = self._crm_ids(account_id, contact_email)
            if not ids:
                return []
            client, company_id, contact_id = ids
            existing = self.store.one("SELECT crm_id FROM crm_links WHERE object_type = 'deal' AND local_ref = %s", (str(account_id),))
            if existing:
                return [f"CRM deal {existing['crm_id']} already open"]
            stage = "appointmentscheduled" if label == "meeting_request" else "qualifiedtobuy"
            name = f"{self.account(account_id)['name']} - {self.config.seller.product}"
            deal_id = client.create_deal(name=name, stage=stage, company_id=company_id, contact_id=contact_id)
            self._link("deal", str(account_id), deal_id)
            self.store.audit(SYSTEM, "crm.deal_created", "account", account_id, {"deal": deal_id, "stage": stage})
            return [f"created CRM deal {deal_id} ({stage})"]
        except CRMError as exc:
            self.store.audit(SYSTEM, "crm.error", "account", account_id, {"error": str(exc)[:200]})
            return [f"CRM sync failed: {str(exc)[:100]}"]

    def export_csv(self, kind: str) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        if kind == "accounts":
            writer.writerow(["domain", "name", "route", "score", "status", "do_not_contact", "contact_name", "contact_title", "contact_email", "top_signals"])
            for r in self.store.all("SELECT * FROM accounts ORDER BY score DESC NULLS LAST, id"):
                contact = r["contact"] or {}
                signals = [s for s in ((r["profile"] or {}).get("signals") or []) if s.get("current")]
                writer.writerow([r["domain"], r["name"], r["route"], r["score"], r["status"], r["do_not_contact"], contact.get("name"), contact.get("title"), contact.get("email"), "; ".join(f"{s['type']}: {s.get('detail') or ''}" for s in signals)])
        elif kind == "contacts":
            writer.writerow(["domain", "name", "title", "email", "persona", "source", "suppressed"])
            for r in self.store.all("SELECT domain, contact FROM accounts WHERE contact IS NOT NULL ORDER BY id"):
                c = r["contact"]
                writer.writerow([r["domain"], c["name"], c["title"], c["email"], c["persona"], c["source"], bool(self.store.suppressed(c["email"] or ""))])
        elif kind == "activities":
            writer.writerow(["time", "type", "domain", "email", "step_or_label", "subject", "status"])
            for r in self.store.all("SELECT m.*, a.domain FROM messages m JOIN accounts a ON a.id = m.account_id ORDER BY m.scheduled_at"):
                writer.writerow([(r["sent_at"] or r["scheduled_at"]).isoformat(), "email", r["domain"], r["to_email"], r["step"], r["subject"], r["status"]])
            for r in self.store.all("SELECT r.*, a.domain FROM replies r LEFT JOIN accounts a ON a.id = r.account_id ORDER BY r.received_at"):
                writer.writerow([r["received_at"].isoformat(), "reply", r["domain"], r["from_email"], r["label"], r["subject"], "received"])
        else:
            raise ScoutError(f"unknown export {kind!r} (accounts, contacts, activities)")
        return buffer.getvalue()

    # ------------------------------------------------------------------ metrics
    def funnel(self) -> dict[str, Any]:
        q = self.store.scalar
        return {
            "accounts": q("SELECT count(*) FROM accounts"),
            "researched": q("SELECT count(*) FROM accounts WHERE profile IS NOT NULL"),
            "qualified": q("SELECT count(*) FROM accounts WHERE route = 'qualified'"),
            "nurture": q("SELECT count(*) FROM accounts WHERE route = 'nurture'"),
            "disqualified": q("SELECT count(*) FROM accounts WHERE route = 'disqualified'"),
            "drafted": q("SELECT count(DISTINCT account_id) FROM drafts"),
            "pending_review": q("SELECT count(*) FROM drafts WHERE status = 'pending'"),
            "approved": q("SELECT count(*) FROM drafts WHERE status = 'approved'"),
            "rejected": q("SELECT count(*) FROM drafts WHERE status = 'rejected'"),
            "sent": q("SELECT count(*) FROM messages WHERE status = 'sent'"),
            "scheduled": q("SELECT count(*) FROM messages WHERE status IN ('scheduled', 'deferred')"),
            "replies": q("SELECT count(*) FROM replies"),
            "positive_replies": q("SELECT count(*) FROM replies WHERE label IN ('interested', 'meeting_request')"),
            "meetings": q("SELECT count(*) FROM meetings"),
            "suppressed": q("SELECT count(*) FROM suppression"),
            "injection_findings": q("SELECT count(*) FROM audit_log WHERE action = 'injection.quarantined'"),
        }


__all__ = ["DraftVariant", "Email", "Scout", "ScoutError"]
