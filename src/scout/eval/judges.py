"""Evaluation-only model tasks: the independent claim audit and the blind preference judge.

Both use a different free model from the one that drafts. The audit does not see Scout's citations: it gets the
ground-truth facts from data/companies.yaml (what is really on the company's site) and the seller's offer, and
lists every specific claim in each email with a supported / unsupported verdict. The preference judge sees two
emails in random order, without knowing which one Scout wrote.
"""

from __future__ import annotations

import hashlib
from typing import Any

from scout.icp import Config
from scout.llm.base import ChatModel, LLMError, Message
from scout.research.agent import complete_json
from scout.synthetic.spec import CompanySpec

AUDIT_SYSTEM = """SCOUT_TASK: audit_claims
You audit cold emails for factual accuracy. You get reference facts about the prospect company (complete and
correct as of {today}) and the sender's own offer. For each email, list every specific factual claim it makes
about the prospect (their company, people, hiring, funding, expansion, tools, size, places, dates) or about the
sender's customers and results, and mark each claim supported only if the reference facts state it (timing words
like "recently" or "new" must match the dates). Generic pleasantries, questions and offers to talk are not claims.

Reply with JSON only:
{{"emails": [{{"id": "...", "claims": [{{"text": "...", "supported": true, "reason": "..."}}]}}]}}"""

PREFERENCE_SYSTEM = """SCOUT_TASK: preference
You are an experienced head of operations at the company named below. Two different vendors sent you a first
email. Which one would you be more likely to answer? Judge relevance to your company, specificity, credibility and
tone. You do not know how either email was written.

Reply with JSON only: {{"winner": "X" or "Y" or "tie", "reason": "..."}}"""


def reference_facts(spec: CompanySpec, config: Config) -> str:
    rows = [
        f"Company: {spec.name} ({spec.domain}), {spec.industry}, headquartered in {spec.hq} ({spec.country}), founded {spec.founded}",
        f"Employees: about {spec.employees}",
        f"Fleet: {spec.fleet_size} {spec.fleet_noun}" if spec.fleet_size else "Fleet: not stated",
        f"Serves: {spec.served}",
    ]
    for sig in spec.signals:
        when = f" (dated {sig.date.isoformat()})" if sig.date else ""
        rows.append(f"Website says{when}: {sig.text}" + ("" if sig.current else " [old news]"))
    rows.append("Sender's proof points: " + " | ".join(p.text for p in config.seller.proof_points))
    rows.append("Sender's product: " + config.seller.one_liner + " " + " ".join(v.text for v in config.seller.value_props))
    return "\n".join(rows)


def audit_emails(
    model: ChatModel, spec: CompanySpec, config: Config, emails: dict[str, str], *, today: str
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """emails: id -> text. Returns id -> claims with verdicts, and the number of model calls."""
    body = "\n\n".join(f"=== Email {key}\n{text}" for key, text in emails.items())
    messages: list[Message] = [
        {"role": "system", "content": AUDIT_SYSTEM.format(today=today)},
        {"role": "user", "content": f"Reference facts:\n{reference_facts(spec, config)}\n\nEmails:\n{body}"},
    ]
    try:
        data, calls = complete_json(model, messages, max_tokens=5000)
    except LLMError:
        return {}, 1
    out: dict[str, list[dict[str, Any]]] = {}
    for item in data.get("emails") or []:  # type: ignore[union-attr]
        if isinstance(item, dict) and str(item.get("id")) in emails:
            out[str(item["id"])] = [c for c in item.get("claims") or [] if isinstance(c, dict)]
    return out, calls


def generic_template(first_name: str, company: str, config: Config) -> str:
    seller = config.seller
    return (
        f"Hi {first_name},\n\nI'm {seller.sender_name.split()[0]} from {seller.company}. We help fleets like {company} reduce "
        f"fuel costs and improve driver safety with telematics. Would you be open to a 20-minute call next week?\n\n"
        f"{seller.sender_name.split()[0]}"
    )


def prefer(model: ChatModel, *, company: str, title: str, scout_email: str, baseline_email: str, key: str) -> tuple[str, str, int]:
    """Returns ('scout' | 'baseline' | 'tie' | 'error', reason, calls). Order is decided by a hash of `key`."""
    scout_first = int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2 == 0
    x, y = (scout_email, baseline_email) if scout_first else (baseline_email, scout_email)
    messages: list[Message] = [
        {"role": "system", "content": PREFERENCE_SYSTEM},
        {"role": "user", "content": f"Your company: {company}. Your role: {title}.\n\n=== Email X\n{x}\n\n=== Email Y\n{y}"},
    ]
    try:
        data, calls = complete_json(model, messages, max_tokens=1500)
    except LLMError as exc:
        return "error", str(exc)[:100], 1
    winner = str(data.get("winner") or "").strip().upper()
    reason = str(data.get("reason") or "")[:300]
    if winner not in {"X", "Y"}:
        return "tie", reason, calls
    scout_won = (winner == "X") == scout_first
    return ("scout" if scout_won else "baseline"), reason, calls
