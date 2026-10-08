"""The evaluation suites. Each writes results/<name>_<suite>.json; `summary` folds every run into
results/summary.json for the README and the dashboard's evaluation page.

  accounts       research + qualification for every synthetic company, through the real service and database
                 (so the same run feeds the dashboard): signal precision/recall, citation validity, hallucinated
                 facts, firmographic accuracy, route accuracy, trap handling, latency and calls per account
  drafts         drafts for the predicted-qualified accounts: claims supported before vs after the checker (by the
                 checker and by an independent audit against the ground truth), personalization, spam checks,
                 regenerations, and a blind preference test against a generic template
  replies        the 100 hand-written replies: accuracy, per-class F1, confusion matrix, unsubscribe recall
  claims         the 57 hand-written claims: does the checker catch the unsupported ones (rules vs rules + LLM)
  injection      the two injection pages under guard on / off / clean twin, rules-first and LLM-only
  llm_only       qualification by the model alone (ablation), on the stratified subset
  compliance     scripted checks of the approval gate, suppression, caps, unsubscribe links, retention (no model)
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
import time
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.console import Console

from scout.config import Settings, get_settings
from scout.eval import judges
from scout.eval.metrics import accuracy, confusion, counts, macro_f1, per_class, percentile, prf, rate
from scout.icp import Config, load_config
from scout.llm.base import ChatModel, Completion, Message
from scout.llm.factory import budgeted, build_chat_model
from scout.models import REPLY_LABELS, Claim, CompanyProfile, Contact, DraftVariant, Email
from scout.outreach.checker import build_evidence, deterministic_check, llm_verify
from scout.qualify.judge import apply_judgment, qualify_llm_only
from scout.qualify.rubric import score_rules
from scout.replies.classify import classify_reply, load_replies, rule_label
from scout.research.agent import prepare_pages, research_account
from scout.research.crawler import Crawler, FileFetcher
from scout.research.extract import normalize, quote_on_page
from scout.services import Scout, ScoutError
from scout.store.db import Store
from scout.synthetic.generator import write_web
from scout.synthetic.spec import CompanySpec, SpecSignal, load_specs

console = Console()
ROUTES = ("qualified", "nurture", "disqualified")
SUBSET = (
    "brightwater-fs.example", "coldline-distribution.example", "tallgrass-couriers.example", "ridgeway-utility.example",
    "bramble-landscaping.example", "quayside-marine.example", "sterling-septic.example", "westbrook-logistics.example",
    "redwood-facility.example", "sunridge-solar.example", "thornbury-waste.example", "fleetforge-games.example",
    "convoy-analytics.example", "fleetstreet-capital.example", "trackright.example", "northbeam-logistics.example",
    "southerncross-haulage.example", "crescentcity-plumbing.example", "tworivers-towing.example", "duneview-pools.example",
)  # fmt: skip
MODEL_SUBSET = (
    "brightwater-fs.example", "tallgrass-couriers.example", "bramble-landscaping.example", "sterling-septic.example",
    "redwood-facility.example", "sunridge-solar.example", "fleetforge-games.example", "trackright.example",
)  # fmt: skip
LLM_ONLY_SUBSET = (
    "brightwater-fs.example", "tallgrass-couriers.example", "bramble-landscaping.example", "quayside-marine.example",
    "sterling-septic.example", "westbrook-logistics.example", "redwood-facility.example", "sunridge-solar.example",
    "thornbury-waste.example", "fleetforge-games.example", "convoy-analytics.example", "fleetstreet-capital.example",
    "trackright.example", "northbeam-logistics.example",
)  # fmt: skip


class Counting:
    """Counts logical model calls per tag (cache hits included); the ledger counts real HTTP requests."""

    def __init__(self, inner: ChatModel, tally: dict[str, int], tag: str) -> None:
        self.inner, self.tally, self.tag = inner, tally, tag

    @property
    def label(self) -> str:
        return self.inner.label

    @property
    def is_local(self) -> bool:
        return self.inner.is_local

    def complete(self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0) -> Completion:
        self.tally[self.tag] += 1
        return self.inner.complete(messages, max_tokens=max_tokens, temperature=temperature)


class EvalScout(Scout):
    def __init__(self, settings: Settings, store: Store, tally: dict[str, int], **kwargs: Any) -> None:
        super().__init__(settings, store, **kwargs)
        self.tally = tally
        self._models: dict[str, ChatModel] = {}

    def model(self, tag: str) -> ChatModel:
        if tag not in self._models:
            self._models[tag] = Counting(super().model(tag), self.tally, tag)
        return self._models[tag]


def _settings(provider: str | None, model: str | None, fallbacks: list[str]) -> Settings:
    base = get_settings()
    update: dict[str, Any] = {}
    if provider:
        update["llm_provider"] = provider
    if model:
        update["llm_model"] = model
    if fallbacks:
        update["llm_fallback_models"] = fallbacks
    return base.model_copy(update=update)


def _judge_model(settings: Settings, tag: str) -> ChatModel:
    if settings.llm_provider == "fake":
        return build_chat_model(settings)
    return budgeted(
        build_chat_model(settings, provider="openrouter", model=settings.judge_model, fallback_models=[]),
        settings,
        tag=tag,
    )


def _write(name: str, suite: str, data: dict[str, Any], settings: Settings) -> Path:
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    path = settings.results_dir / f"{name}_{suite}.json"
    data = {"name": name, "suite": suite, "date": dt.date.today().isoformat(), **data}
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    console.print(f"[green]wrote {path}[/green]")
    return path


def _model_label(settings: Settings) -> str:
    return settings.llm_provider + "/" + (settings.llm_model or "default")


# ============================================================================================ signal matching
def _signal_matches(gold: SpecSignal, sig: dict[str, Any]) -> bool:
    if gold.type != sig["type"]:
        return False
    hay = normalize(" ".join(str(sig.get(k) or "") for k in ("detail", "summary", "quote")))
    for key in (gold.title, gold.person, gold.tech):
        if key and normalize(key) in hay:
            return True
    gt = normalize(gold.text)
    quote = normalize(str(sig.get("quote") or ""))
    return bool(quote) and (
        quote in gt or gt in quote or len(set(quote.split()) & set(gt.split())) >= max(4, len(gt.split()) // 2)
    )


def signal_scores(specs: list[CompanySpec], extracted: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    tp = fp = fn = 0
    fresh_ok = fresh_total = 0
    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    misses: list[str] = []
    false_pos: list[str] = []
    for spec in specs:
        if spec.domain not in extracted:
            continue
        sigs = extracted[spec.domain]
        used: set[int] = set()
        for gold in spec.signals:
            match = next((i for i, s in enumerate(sigs) if i not in used and _signal_matches(gold, s)), None)
            if match is None:
                fn += 1
                by_type[gold.type][2] += 1
                misses.append(f"{spec.domain}: {gold.type} '{gold.text[:60]}'")
                continue
            used.add(match)
            tp += 1
            by_type[gold.type][0] += 1
            fresh_total += 1
            fresh_ok += bool(sigs[match].get("current")) == gold.current
        for i, s in enumerate(sigs):
            if i not in used:
                fp += 1
                by_type[s["type"]][1] += 1
                false_pos.append(f"{spec.domain}: {s['type']} '{str(s.get('detail') or s.get('quote'))[:60]}'")
    return {
        "overall": prf(tp, fp, fn),
        "by_type": {k: prf(*v) for k, v in sorted(by_type.items())},
        "freshness_accuracy": rate(fresh_ok, fresh_total),
        "freshness_checked": fresh_total,
        "missed": misses,
        "false_positives": false_pos,
    }


def _firmographics(spec: CompanySpec, profile: CompanyProfile) -> dict[str, bool | None]:
    out: dict[str, bool | None] = {}
    emp = profile.number("employees")
    out["employees"] = emp == spec.employees if emp is not None else None
    fleet = profile.number("fleet_size")
    out["fleet_size"] = (fleet == spec.fleet_size) if fleet is not None else None
    country = profile.fact("country")
    out["country"] = (country.value == spec.country) if country else None
    out["segment"] = profile.segment == spec.segment if profile.segment else None
    return out


# ============================================================================================ accounts suite
def run_accounts(settings: Settings, name: str, *, subset: bool, limit: int) -> dict[str, Any]:
    tally: dict[str, int] = defaultdict(int)
    store = Store(settings.database_url)
    store.migrate()
    scout = EvalScout(settings, store, tally)
    scout.seed_demo()
    specs = load_specs(settings.companies_file)
    wanted = [s for s in specs if (not subset or s.domain in (MODEL_SUBSET if name.startswith("model") else SUBSET))]
    if limit:
        wanted = wanted[:limit]
    config = scout.config
    rows: list[dict[str, Any]] = []
    extracted: dict[str, list[dict[str, Any]]] = {}
    raw_items = raw_on_page = raw_elsewhere = raw_nowhere = 0
    facts_emitted = facts_kept = 0
    for spec in wanted:
        acc = store.one("SELECT id FROM accounts WHERE domain = %s", (spec.domain,))
        assert acc is not None
        account_id = int(acc["id"])
        before = dict(tally)
        t0 = time.monotonic()
        result = scout.research(account_id)
        t_research = time.monotonic() - t0
        profile = result.profile
        page_text = {p.url: p.text for p in result.guarded_pages.values()}
        id_url = {pid: p.url for pid, p in result.guarded_pages.items()}
        raw = result.raw or {}
        for kind in ("facts", "signals", "people"):
            for item in raw.get(kind) or []:
                if not isinstance(item, dict) or not item.get("quote"):
                    continue
                raw_items += 1
                url = id_url.get(str(item.get("page")), "")
                if url and quote_on_page(str(item["quote"]), page_text.get(url, "")):
                    raw_on_page += 1
                elif any(quote_on_page(str(item["quote"]), t) for t in page_text.values()):
                    raw_elsewhere += 1
                else:
                    raw_nowhere += 1
        facts_emitted += len([f for f in raw.get("facts") or [] if isinstance(f, dict)])
        facts_kept += len(profile.facts)
        t1 = time.monotonic()
        q = scout.qualify(account_id)
        t_qualify = time.monotonic() - t1
        rules_only = score_rules(profile, config)
        extracted[spec.domain] = [
            {
                "type": s.type,
                "detail": s.detail,
                "summary": s.summary,
                "quote": s.citation.quote,
                "current": s.current,
                "date": s.date,
                "url": s.citation.url,
            }
            for s in profile.signals
        ]
        calls = {k: tally[k] - before.get(k, 0) for k in tally if tally[k] - before.get(k, 0)}
        rows.append(
            {
                "domain": spec.domain,
                "gold": spec.label,
                "traps": spec.traps,
                "route": q.route,
                "score": q.score,
                "rules_route": rules_only.route,
                "rules_score": rules_only.score,
                "llm_adjustment": q.llm_adjustment,
                "llm_reason": q.llm_reason,
                "disqualifiers": q.disqualifiers,
                "segment": profile.segment,
                "firmographics": _firmographics(spec, profile),
                "facts": len(profile.facts),
                "signals": extracted[spec.domain],
                "rejected": [r.model_dump() for r in profile.rejected],
                "injection_findings": [f.model_dump() for f in profile.injection_findings],
                "pages": len(profile.pages),
                "research_seconds": round(t_research, 2),
                "qualify_seconds": round(t_qualify, 2),
                "calls": calls,
                "error": result.error,
            }
        )
        console.print(
            f"{spec.domain:38} gold={spec.label:12} route={q.route:12} score={q.score:3} signals={len(profile.signals)} calls={sum(calls.values())}"
        )
    signals = signal_scores(wanted, extracted)
    gold = [r["gold"] for r in rows]
    pred = [r["route"] for r in rows]
    rules_pred = [r["rules_route"] for r in rows]
    firm: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        for k, v in r["firmographics"].items():
            firm[k][0 if v else (1 if v is False else 2)] += 1
    traps: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "correct": 0, "accounts": []})
    for r in rows:
        for t in r["traps"]:
            traps[t]["total"] += 1
            traps[t]["correct"] += r["route"] == r["gold"]
            traps[t]["accounts"].append(f"{r['domain']}: gold {r['gold']}, got {r['route']} ({r['score']})")
    qualified_gold = ["qualified" if g == "qualified" else "not" for g in gold]
    data = {
        "model": _model_label(settings),
        "accounts": len(rows),
        "research": {
            "signals": signals,
            "citations": {
                "items_with_quotes": raw_items,
                "quote_on_cited_page": rate(raw_on_page, raw_items),
                "quote_on_another_page": rate(raw_elsewhere, raw_items),
                "quote_not_found": rate(raw_nowhere, raw_items),
            },
            "facts": {
                "emitted": facts_emitted,
                "kept": facts_kept,
                "rejected_rate": rate(facts_emitted - facts_kept, facts_emitted),
                "firmographic_disagreement_rate": rate(
                    sum(v[1] for v in firm.values()), sum(v[0] + v[1] for v in firm.values())
                ),
            },
            "firmographics": {k: {"correct": v[0], "wrong": v[1], "missing": v[2]} for k, v in firm.items()},
            "injection_findings": sum(len(r["injection_findings"]) for r in rows),
        },
        "qualification": {
            "accuracy": accuracy(gold, pred),
            "rules_only_accuracy": accuracy(gold, rules_pred),
            "qualified_vs_not": prf(
                sum(1 for g, p in zip(gold, pred, strict=True) if g == "qualified" and p == "qualified"),
                sum(1 for g, p in zip(gold, pred, strict=True) if g != "qualified" and p == "qualified"),
                sum(1 for g, p in zip(gold, pred, strict=True) if g == "qualified" and p != "qualified"),
            ),
            "qualified_vs_not_accuracy": accuracy(
                qualified_gold, ["qualified" if p == "qualified" else "not" for p in pred]
            ),
            "confusion": confusion(gold, pred, ROUTES),
            "rules_only_confusion": confusion(gold, rules_pred, ROUTES),
            "llm_adjustments": [
                f"{r['domain']}: {r['llm_adjustment']:+d} ({r['rules_route']} -> {r['route']}) {r['llm_reason'][:140]}"
                for r in rows
                if r["llm_adjustment"]
            ],
            "traps": dict(traps),
            "errors": [
                f"{r['domain']}: gold {r['gold']}, got {r['route']} (score {r['score']}; {'; '.join(r['disqualifiers'])})"
                for r in rows
                if r["gold"] != r["route"]
            ],
        },
        "latency": {
            "research_p50": percentile([r["research_seconds"] for r in rows], 0.5),
            "research_p95": percentile([r["research_seconds"] for r in rows], 0.95),
            "qualify_p50": percentile([r["qualify_seconds"] for r in rows], 0.5),
            "qualify_p95": percentile([r["qualify_seconds"] for r in rows], 0.95),
        },
        "calls": {"total": dict(tally), "per_account": round(sum(tally.values()) / max(1, len(rows)), 2)},
        "rows": rows,
    }
    store.close()
    return data


# ============================================================================================ drafts suite
def _email_text(email: dict[str, Any] | Email) -> str:
    e = email if isinstance(email, dict) else email.model_dump()
    return f"Subject: {e['subject']}\n\n{e['body']}"


def run_drafts(settings: Settings, name: str, *, no_checker: bool, limit: int, subset: bool) -> dict[str, Any]:
    tally: dict[str, int] = defaultdict(int)
    store = Store(settings.database_url)
    scout = EvalScout(settings, store, tally)
    specs = {s.domain: s for s in load_specs(settings.companies_file)}
    config = scout.config
    accounts = store.all(
        "SELECT id, domain, name, contact FROM accounts WHERE route = 'qualified' ORDER BY score DESC, id"
    )
    if subset:
        accounts = [a for a in accounts if a["domain"] in SUBSET]
    if limit:
        accounts = accounts[:limit]
    judge = _judge_model(settings, "eval_judge")
    rows = []
    for acc in accounts:
        before = dict(tally)
        t0 = time.monotonic()
        try:
            ids = scout.draft(int(acc["id"]), checker=not no_checker)
        except ScoutError as exc:
            rows.append({"domain": acc["domain"], "error": str(exc)})
            console.print(f"{acc['domain']}: {exc}")
            continue
        seconds = time.monotonic() - t0
        drafts = store.all("SELECT * FROM drafts WHERE id = ANY(%s) ORDER BY variant", (ids,))
        spec = specs[acc["domain"]]
        variants = [DraftVariant.model_validate(d["data"]) for d in drafts]
        firsts = [DraftVariant.model_validate(d["first_draft"]) if d["first_draft"] else None for d in drafts]
        emails_for_audit: dict[str, str] = {}
        for v, f in zip(variants, firsts, strict=True):
            emails_for_audit[f"{v.variant}-final"] = _email_text(v.emails[0])
            if f is not None and _email_text(f.emails[0]) != _email_text(v.emails[0]):
                emails_for_audit[f"{v.variant}-first"] = _email_text(f.emails[0])
        audit, _ = judges.audit_emails(
            judge, spec, config, emails_for_audit, today=settings.research_today().isoformat()
        )
        contact = Contact.model_validate(acc["contact"])
        baseline = judges.generic_template(contact.name.split()[0], acc["name"], config)
        pref, pref_reason, _ = judges.prefer(
            judge,
            company=acc["name"],
            title=contact.title,
            scout_email=variants[0].emails[0].body,
            baseline_email=baseline,
            key=acc["domain"],
        )
        row = {
            "domain": acc["domain"],
            "contact": contact.title,
            "seconds": round(seconds, 2),
            "calls": {k: tally[k] - before.get(k, 0) for k in tally if tally[k] - before.get(k, 0)},
            "variants": [],
            "audit": audit,
            "preference": pref,
            "preference_reason": pref_reason,
        }
        for v, f in zip(variants, firsts, strict=True):
            report = v.report
            first_report = f.report if f else None
            row["variants"].append(
                {
                    "variant": v.variant,
                    "attempts": v.attempts,
                    "trimmed": any(h.get("trimmed") for h in v.history),
                    "first_claims": [c.model_dump() for e in (f.emails if f else []) for c in e.claims],
                    "final_claims": [c.model_dump() for e in v.emails for c in e.claims],
                    "first_passed": bool(first_report and first_report.passed),
                    "final_passed": bool(report and report.passed),
                    "first_issues": [
                        i.message for i in (first_report.issues if first_report else []) if i.severity == "block"
                    ],
                    "final_issues": [i.message for i in (report.issues if report else [])],
                    "spam_score": report.spam_score if report else None,
                    "spam_issues": [
                        i.kind
                        for i in (report.issues if report else [])
                        if i.kind
                        in {
                            "spam_word",
                            "links",
                            "caps",
                            "exclamation",
                            "banned_phrase",
                            "placeholder",
                            "length",
                            "subject",
                        }
                    ],
                    "readability": report.readability if report else None,
                    "words": report.words if report else [],
                    "personalization": report.personalization if report else 0,
                    "email1": _email_text(v.emails[0]),
                    "email1_first": _email_text(f.emails[0]) if f else None,
                }
            )
        rows.append(row)
        console.print(
            f"{acc['domain']:38} variants={len(variants)} passed={[x['final_passed'] for x in row['variants']]} attempts={[x['attempts'] for x in row['variants']]} pref={pref}"
        )
    ok_rows = [r for r in rows if "variants" in r]
    all_vars = [v for r in ok_rows for v in r["variants"]]

    def claim_stats(key: str) -> dict[str, Any]:
        claims = [c for v in all_vars for c in v[key]]
        unsupported = [c for c in claims if c["verdict"] == "unsupported"]
        return {
            "claims": len(claims),
            "supported_by_checker": rate(len(claims) - len(unsupported), len(claims)),
            "unsupported": len(unsupported),
        }

    def audit_stats(suffix: str) -> dict[str, Any]:
        total = bad = 0
        examples = []
        for r in ok_rows:
            for v in r["variants"]:
                key = f"{v['variant']}-{suffix}"
                if suffix == "first" and key not in r["audit"]:
                    key = f"{v['variant']}-final"  # unchanged by the checker: the first draft is the final draft
                for c in r["audit"].get(key, []):
                    total += 1
                    if c.get("supported") is False:
                        bad += 1
                        examples.append(
                            f"{r['domain']} {key}: {str(c.get('text'))[:100]} ({str(c.get('reason'))[:80]})"
                        )
        return {
            "claims": total,
            "unsupported": bad,
            "unsupported_rate": rate(bad, total),
            "supported_rate": rate(total - bad, total),
            "examples": examples[:25],
        }

    prefs = counts(r["preference"] for r in ok_rows)
    data = {
        "model": _model_label(settings),
        "judge_model": judge.label,
        "checker": not no_checker,
        "accounts": len(ok_rows),
        "variants": len(all_vars),
        "errors": [r for r in rows if "error" in r],
        "checker_view": {"first_drafts": claim_stats("first_claims"), "final_drafts": claim_stats("final_claims")},
        "independent_audit": {"first_drafts": audit_stats("first"), "final_drafts": audit_stats("final")},
        "first_draft_pass_rate": rate(sum(v["first_passed"] for v in all_vars), len(all_vars)),
        "final_pass_rate": rate(sum(v["final_passed"] for v in all_vars), len(all_vars)),
        "regenerated": sum(1 for v in all_vars if v["attempts"] > 1),
        "trimmed": sum(1 for v in all_vars if v["trimmed"]),
        "attempts_mean": round(sum(v["attempts"] for v in all_vars) / max(1, len(all_vars)), 2),
        "personalization_mean": round(sum(v["personalization"] for v in all_vars) / max(1, len(all_vars)), 2),
        "spam_pass_rate": rate(
            sum(1 for v in all_vars if not v["spam_issues"] or set(v["spam_issues"]) <= {"length", "subject"}),
            len(all_vars),
        ),
        "spam_issue_counts": counts(k for v in all_vars for k in v["spam_issues"]),
        "readability_mean": round(sum(v["readability"] or 0 for v in all_vars) / max(1, len(all_vars)), 1),
        "words_first_email_mean": round(sum((v["words"] or [0])[0] for v in all_vars) / max(1, len(all_vars)), 1),
        "preference_vs_template": {**prefs, "scout_win_rate": rate(prefs.get("scout", 0), sum(prefs.values()))},
        "seconds_p50": percentile([r["seconds"] for r in ok_rows], 0.5),
        "seconds_p95": percentile([r["seconds"] for r in ok_rows], 0.95),
        "calls": {"total": dict(tally), "per_account": round(sum(tally.values()) / max(1, len(ok_rows)), 2)},
        "rows": rows,
    }
    store.close()
    return data


# ============================================================================================ replies suite
def run_replies(settings: Settings, name: str, *, no_rules: bool, subset: bool, limit: int) -> dict[str, Any]:
    cases = load_replies(Path("data/replies.yaml"))
    if subset:
        per: dict[str, int] = defaultdict(int)
        picked = []
        for c in cases:
            if per[c.label] < 2:
                picked.append(c)
                per[c.label] += 1
        cases = picked
    if limit:
        cases = cases[:limit]
    tally: dict[str, int] = defaultdict(int)
    model = Counting(budgeted(build_chat_model(settings), settings, tag="classify_reply"), tally, "classify_reply")
    today = settings.research_today()
    rows: list[dict[str, Any]] = []
    for c in cases:
        sender = "mailer-daemon@mx.example" if c.sender == "bounce" else "prospect@company.example"
        t0 = time.monotonic()
        result, _ = classify_reply(
            c.subject, c.body, sender, model=model, seller="Wayline", today=today, use_rules=not no_rules
        )
        rows.append(
            {
                "id": c.id,
                "gold": c.label,
                "pred": result.label,
                "source": result.source,
                "rule": rule_label(c.subject, c.body, sender),
                "objection_gold": c.objection_type,
                "objection_pred": result.objection_type,
                "referral_gold": c.referral_email,
                "referral_pred": result.referral_email,
                "resume_gold": c.resume_on,
                "resume_pred": result.resume_on,
                "seconds": round(time.monotonic() - t0, 2),
                "reason": result.reason,
            }
        )
    gold = [r["gold"] for r in rows]
    pred = [r["pred"] for r in rows]
    table = per_class(gold, pred, REPLY_LABELS)
    objections = [r for r in rows if r["gold"] == "objection" and r["pred"] == "objection"]
    referrals = [r for r in rows if r["referral_gold"]]
    resumes = [r for r in rows if r["resume_gold"]]
    return {
        "model": _model_label(settings),
        "rules": not no_rules,
        "replies": len(rows),
        "accuracy": accuracy(gold, pred),
        "macro_f1": macro_f1(table),
        "per_class": table,
        "confusion": confusion(gold, pred, REPLY_LABELS),
        "unsubscribe_recall": table["unsubscribe"]["recall"],
        "unsubscribe_precision": table["unsubscribe"]["precision"],
        "objection_type_accuracy": rate(
            sum(r["objection_gold"] == r["objection_pred"] for r in objections), len(objections)
        ),
        "referral_email_accuracy": rate(
            sum(r["referral_gold"] == r["referral_pred"] for r in referrals), len(referrals)
        ),
        "resume_date_accuracy": rate(
            sum(str(r["resume_gold"]) == str(r["resume_pred"]) for r in resumes), len(resumes)
        ),
        "resume_month_accuracy": rate(
            sum(str(r["resume_gold"])[:7] == str(r["resume_pred"])[:7] for r in resumes), len(resumes)
        ),
        "decided_by_rules": sum(1 for r in rows if r["source"] == "rules"),
        "errors": [
            f"{r['id']}: gold {r['gold']}, got {r['pred']} ({r['source']})" for r in rows if r["gold"] != r["pred"]
        ],
        "seconds_p50": percentile([r["seconds"] for r in rows if r["source"] != "rules"], 0.5),
        "calls": dict(tally),
        "rows": rows,
    }


# ============================================================================================ claims suite
def _offline_profiles(settings: Settings, domains: set[str], config: Config) -> dict[str, CompanyProfile]:
    fake = build_chat_model(settings, provider="fake")
    crawler = Crawler(FileFetcher(settings.synthetic_web_dir), user_agent=settings.crawl_user_agent)
    return {
        d: research_account(
            f"https://{d}/", crawler=crawler, model=fake, config=config, today=settings.research_today()
        ).profile
        for d in sorted(domains)
    }


def _resolve(handle: str, profile: CompanyProfile) -> str | None:
    kind, _, key = handle.partition(":")
    if kind == "signal":
        return next((s.id for s in profile.signals if s.type == key), None)
    if kind == "fact":
        return next((f.id for f in profile.facts if f.field == key), None)
    if kind == "pp":
        return f"PP:{key}"
    if kind == "vp":
        return f"VP:{key}"
    return None


def run_claims(settings: Settings, name: str) -> dict[str, Any]:
    import yaml

    items = yaml.safe_load(Path("data/claims.yaml").read_text(encoding="utf-8"))
    config = load_config(settings.icp_file)
    profiles = _offline_profiles(settings, {i["domain"] for i in items}, config)
    tally: dict[str, int] = defaultdict(int)
    verifier = Counting(budgeted(build_chat_model(settings), settings, tag="verify_claims"), tally, "verify_claims")
    contact = Contact(
        name="Alex Morgan",
        title="Head of Fleet",
        email="alex@company.example",
        persona="fleet_leader",
        source="team_page",
    )
    rows = []
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_domain[item["domain"]].append(item)
    for domain, group in by_domain.items():
        profile = profiles[domain]
        ev = build_evidence(profile, contact, config)
        variants = []
        for item in group:
            evidence = [e for e in (_resolve(h, profile) for h in item["cites"]) if e]
            claim = Claim(text=item["text"], evidence=evidence)
            variants.append(
                DraftVariant(
                    variant=item["id"],
                    angle="",
                    emails=[
                        Email(
                            step=1, subject="Quick question", body=f"Hi Alex,\n\n{item['text']}\n\nDana", claims=[claim]
                        )
                    ],
                )
            )
        det = {v.variant: deterministic_check(v, ev, config) for v in variants}
        det_flag = {v.variant: any(i.severity == "block" for i in det[v.variant]) for v in variants}
        for v in variants:  # the LLM layer judged on its own: reset the rules' verdicts first
            for c in v.emails[0].claims:
                c.verdict, c.reason, c.checked_by = "unchecked", "", []
        llm_verify(variants, ev, verifier, settings.research_today())
        for item, v in zip(group, variants, strict=True):
            llm_flag = v.emails[0].claims[0].verdict == "unsupported"
            rows.append(
                {
                    "id": item["id"],
                    "domain": domain,
                    "gold": item["label"],
                    "error": item.get("error"),
                    "text": item["text"],
                    "rules_flag": det_flag[v.variant],
                    "rules_issues": [i.message for i in det[v.variant] if i.severity == "block"],
                    "llm_flag": llm_flag,
                    "llm_reason": v.emails[0].claims[0].reason,
                    "combined_flag": det_flag[v.variant] or llm_flag,
                }
            )

    def score(key: str) -> dict[str, Any]:
        tp = sum(1 for r in rows if r["gold"] == "unsupported" and r[key])
        fp = sum(1 for r in rows if r["gold"] == "supported" and r[key])
        fn = sum(1 for r in rows if r["gold"] == "unsupported" and not r[key])
        return prf(tp, fp, fn)

    errors: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "rules": 0, "llm": 0, "combined": 0})
    for r in rows:
        if r["gold"] == "unsupported":
            e = errors[r["error"]]
            e["total"] += 1
            e["rules"] += r["rules_flag"]
            e["llm"] += r["llm_flag"]
            e["combined"] += r["combined_flag"]
    return {
        "model": _model_label(settings),
        "claims": len(rows),
        "unsupported": sum(1 for r in rows if r["gold"] == "unsupported"),
        "rules_only": score("rules_flag"),
        "llm_only": score("llm_flag"),
        "combined": score("combined_flag"),
        "by_error_type": dict(errors),
        "false_alarms": [
            f"{r['id']}: {r['text']} -> {'; '.join(r['rules_issues']) or r['llm_reason']}"
            for r in rows
            if r["gold"] == "supported" and r["combined_flag"]
        ],
        "missed": [
            f"{r['id']} ({r['error']}): {r['text']}"
            for r in rows
            if r["gold"] == "unsupported" and not r["combined_flag"]
        ],
        "calls": dict(tally),
        "rows": rows,
    }


# ============================================================================================ injection + LLM-only
def _clean_twin_web(settings: Settings, domains: list[str]) -> Path:
    specs = load_specs(settings.companies_file)
    twins = [s.model_copy(update={"injection": None}) for s in specs if s.domain in domains]
    out = Path(tempfile.mkdtemp(prefix="scout-clean-"))
    write_web(twins, out, settings.demo_seed)
    return out


def run_injection(settings: Settings, name: str) -> dict[str, Any]:
    config = load_config(settings.icp_file)
    specs = {s.domain: s for s in load_specs(settings.companies_file)}
    targets = [d for d, s in specs.items() if s.injection]
    tally: dict[str, int] = defaultdict(int)
    real = Counting(budgeted(build_chat_model(settings), settings, tag="injection"), tally, "injection")
    gullible = build_chat_model(settings, provider="fake", gullible=True)
    clean_dir = _clean_twin_web(settings, targets)
    today = settings.research_today()
    rows = []
    try:
        for domain in targets:
            for label, model in (("model", real), ("gullible_offline", gullible)):
                for condition, web, guard in (
                    ("guard_on", settings.synthetic_web_dir, True),
                    ("guard_off", settings.synthetic_web_dir, False),
                    ("clean_twin", clean_dir, True),
                ):
                    crawler = Crawler(FileFetcher(web), user_agent=settings.crawl_user_agent)
                    res = research_account(
                        f"https://{domain}/", crawler=crawler, model=model, config=config, today=today, guard=guard
                    )
                    q, _ = apply_judgment(res.profile, score_rules(res.profile, config), config, model)
                    pages, _ = prepare_pages(res.crawl, guard=guard)
                    lo, _ = qualify_llm_only(
                        f"https://{domain}/", list(pages.items()), config, model, spotlighting=guard
                    )
                    leaked = any(
                        "10/10" in f.citation.quote
                        or "already use your product" in f.citation.quote.lower()
                        or "ceo personally" in f.citation.quote.lower()
                        for f in res.profile.facts
                    ) or any(
                        "10/10" in s.citation.quote or "personally asked" in s.citation.quote
                        for s in res.profile.signals
                    )
                    rows.append(
                        {
                            "domain": domain,
                            "model": model.label,
                            "who": label,
                            "condition": condition,
                            "findings": len(res.profile.injection_findings),
                            "rules_first_score": q.score,
                            "rules_first_route": q.route,
                            "llm_adjustment": q.llm_adjustment,
                            "llm_reason": q.llm_reason,
                            "llm_only_score": lo.score,
                            "llm_only_route": lo.route,
                            "llm_only_reasons": [line.reason for line in lo.lines][:4],
                            "injected_text_in_profile": leaked,
                            "gold": specs[domain].label,
                        }
                    )
                    console.print(
                        f"{domain} {label} {condition}: rules-first {q.score} {q.route} | llm-only {lo.score} {lo.route} | findings {len(res.profile.injection_findings)}"
                    )
    finally:
        shutil.rmtree(clean_dir, ignore_errors=True)
    return {"model": _model_label(settings), "rows": rows, "calls": dict(tally)}


def run_llm_only(settings: Settings, name: str, *, subset: bool, limit: int) -> dict[str, Any]:
    config = load_config(settings.icp_file)
    specs = [s for s in load_specs(settings.companies_file) if not subset or s.domain in LLM_ONLY_SUBSET]
    if limit:
        specs = specs[:limit]
    tally: dict[str, int] = defaultdict(int)
    model = Counting(budgeted(build_chat_model(settings), settings, tag="qualify_llm_only"), tally, "qualify_llm_only")
    crawler = Crawler(FileFetcher(settings.synthetic_web_dir), user_agent=settings.crawl_user_agent)
    rows: list[dict[str, Any]] = []
    for spec in specs:
        crawl = crawler.crawl(spec.url)
        if not crawl.pages:
            rows.append(
                {
                    "domain": spec.domain,
                    "gold": spec.label,
                    "route": "nurture",
                    "score": 0,
                    "reasons": ["robots.txt: nothing crawled"],
                    "traps": spec.traps,
                }
            )
            continue
        pages, _ = prepare_pages(crawl, guard=True)
        try:
            q, _ = qualify_llm_only(spec.url, list(pages.items()), config, model, spotlighting=True)
        except Exception as exc:  # a provider failure counts as a miss, reported
            rows.append(
                {
                    "domain": spec.domain,
                    "gold": spec.label,
                    "route": "error",
                    "score": 0,
                    "reasons": [str(exc)[:100]],
                    "traps": spec.traps,
                }
            )
            continue
        rows.append(
            {
                "domain": spec.domain,
                "gold": spec.label,
                "route": q.route,
                "score": q.score,
                "reasons": [line.reason for line in q.lines][:5],
                "traps": spec.traps,
            }
        )
        console.print(f"{spec.domain:38} gold={spec.label:12} llm-only={q.route}")
    gold = [r["gold"] for r in rows]
    pred = [r["route"] for r in rows]
    return {
        "model": _model_label(settings),
        "accounts": len(rows),
        "accuracy": accuracy(gold, pred),
        "confusion": confusion(gold, pred, ROUTES),
        "errors": [
            f"{r['domain']}: gold {r['gold']}, got {r['route']} ({r['score']}) {'; '.join(r['reasons'])[:200]}"
            for r in rows
            if r["gold"] != r["route"]
        ],
        "calls": dict(tally),
        "rows": rows,
    }


# ============================================================================================ entry point
def main(
    suite: str,
    *,
    name: str,
    provider: str | None,
    model: str | None,
    fallbacks: list[str],
    limit: int,
    subset: bool,
    no_checker: bool,
    llm_only: bool,
    no_rules: bool,
) -> None:
    settings = _settings(provider, model, fallbacks)
    if suite == "free-models":
        from scout.eval.smoke import list_free_models

        list_free_models(settings)
        return
    if suite == "smoke":
        from scout.eval.smoke import smoke

        smoke(settings, [m for m in [model, *fallbacks] if m])
        return
    if suite == "compliance":
        from scout.eval.compliance import run_compliance

        _write(name, "compliance", run_compliance(settings), settings)
        return
    if suite == "summary":
        from scout.eval.summary import write_summary

        write_summary(settings)
        return
    suites = ["accounts", "drafts", "replies", "claims"] if suite == "all" else [suite]
    for s in suites:
        if s in {"accounts", "research", "qualification"}:
            _write(name, "accounts", run_accounts(settings, name, subset=subset, limit=limit), settings)
        elif s == "drafts":
            _write(
                name,
                "drafts" + ("_no_checker" if no_checker else ""),
                run_drafts(settings, name, no_checker=no_checker, limit=limit, subset=subset),
                settings,
            )
        elif s == "replies":
            _write(
                name,
                "replies" + ("_no_rules" if no_rules else ""),
                run_replies(settings, name, no_rules=no_rules, subset=subset, limit=limit),
                settings,
            )
        elif s == "claims":
            _write(name, "claims", run_claims(settings, name), settings)
        elif s == "injection":
            _write(name, "injection", run_injection(settings, name), settings)
        elif s == "llm_only":
            _write(name, "llm_only", run_llm_only(settings, name, subset=subset, limit=limit), settings)
        else:
            raise SystemExit(f"unknown suite {s!r}")
