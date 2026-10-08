"""Rule-based implementations of every model task, used by the offline `fake` provider.

They read the same prompts a real model gets and answer in the same JSON, so the whole pipeline (validation,
qualification, the claim checker, routing) runs unchanged without an API key. They are written against the
synthetic web's wording, so on the synthetic web they are close to perfect; on real websites they would be crude.
That is why the README reports them as an offline baseline, never as the product's accuracy.

`gullible=True` makes the judgment-style tasks obey instructions they find in page text (the injection tests).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from scout.llm.base import Message
from scout.llm.fake import FakeHandler

_PAGE = re.compile(r'<<PAGE id="(P\d+)" url="([^"]+)"[^>]*>>\n(.*?)\n<</PAGE[^>]*>>', re.DOTALL)
_PLAIN_PAGE = re.compile(r"^PAGE (P\d+) (\S+)\n(.*?)(?=^PAGE P\d+ |\Z)", re.DOTALL | re.MULTILINE)
_LINE = re.compile(r"^\[(P\d+)\.\d+\](?: \(\d{4}-\d{2}-\d{2}\))? (.*)$")

SEGMENT_RULES: tuple[tuple[str, str], ...] = (
    (r"telematics|gps tracking", "telematics_vendor"),
    (r"software|platform|saas", "software"),
    (r"video game|game studio", "games"),
    (r"restaurant|diner", "restaurants"),
    (r"publisher|newsletter|media", "media"),
    (r"investment|asset management|private equity", "finance"),
    (r"dental|clinic", "healthcare_clinics"),
    (r"locker", "hardware"),
    (r"bicycle rental|bike rental", "leisure_rental"),
    (r"travel agency", "travel"),
    (r"florist", "retail"),
    (r"coffee|brewery|roast", "manufacturing"),
    (r"imaging|diagnostic", "mobile_healthcare"),
    (r"medical transport|shuttle|school transportation|bus|passenger", "passenger_transport"),
    (r"courier|parcel|last-mile|same-day|delivery", "last_mile_delivery"),
    (r"waste|recycling", "waste_management"),
    (r"utility|power line", "utilities_contracting"),
    (r"equipment rental", "equipment_rental"),
    (r"construction", "construction"),
    (r"food|beverage|dairy", "food_beverage_distribution"),
    (r"office supplies|wholesale", "wholesale_distribution"),
    (r"trucking|freight|haul|logistics|moving", "freight_trucking"),
    (r"plumbing|hvac|electric|pest|landscap|tree care|elevator|facility|maintenance|appliance|septic|pool|security|solar|linen|marine|towing|repair|heating", "field_services"),
)

FLEET_ROLE_WORDS = ("fleet", "dispatch", "transport manager", "transportation safety", "safety & training", "telematics")
NOT_FLEET_ROLES = ("driver", "courier", "designer", "engineer", "programmer", "data", "support engineer")
TECH = ("ServiceTitan", "Salesforce Field Service", "SAP", "Microsoft Dynamics 365", "Oracle NetSuite")
COMPETITORS = ("TrackRight", "GeoPulse")
TITLE_WORDS = (
    "Chief Executive Officer", "Chief Operating Officer", "Chief Financial Officer", "Chief Technology Officer",
    "Managing Director", "VP Fleet Operations", "VP Operations", "Director of Operations", "Operations Director",
    "Head of Fleet", "Fleet Operations Manager", "Fleet Manager", "Safety Director", "HR Director",
    "Marketing Manager", "Creative Director", "Head of Product",
)  # fmt: skip
COUNTRY_CODES = {
    "united states": "US", "canada": "CA", "united kingdom": "GB", "ireland": "IE", "germany": "DE",
    "netherlands": "NL", "belgium": "BE", "france": "FR", "austria": "AT", "norway": "NO", "sweden": "SE",
    "denmark": "DK", "finland": "FI", "australia": "AU", "peru": "PE", "japan": "JP",
}  # fmt: skip


def _system(messages: Sequence[Message]) -> str:
    return next((m["content"] for m in messages if m["role"] == "system"), "")


def _user(messages: Sequence[Message]) -> str:
    return next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")


def parse_pages(prompt: str) -> list[tuple[str, str, list[str]]]:
    """(page id, url, block texts) from a spotlighted or plain prompt."""
    pages = []
    for match in _PAGE.finditer(prompt):
        lines = []
        for line in match.group(3).splitlines():
            m = _LINE.match(line)
            lines.append(m.group(2) if m else line)
        pages.append((match.group(1), match.group(2), lines))
    if not pages:
        for match in _PLAIN_PAGE.finditer(prompt):
            pages.append((match.group(1), match.group(2), [ln for ln in match.group(3).splitlines() if ln.strip()]))
    return pages


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if s.strip()]


# ------------------------------------------------------------------------------------------------ extract
def extract(messages: Sequence[Message], gullible: bool) -> str:
    pages = parse_pages(_user(messages))
    out: dict[str, Any] = {"company_name": None, "segment": None, "industry": None, "facts": [], "signals": [], "people": []}
    facts: dict[str, dict[str, str]] = {}
    hq_city = ""

    def fact(field: str, value: str, page: str, quote: str) -> None:
        if field not in facts:
            facts[field] = {"field": field, "value": value, "page": page, "quote": quote}

    def signal(kind: str, summary: str, detail: str | None, page: str, quote: str) -> None:
        out["signals"].append({"type": kind, "summary": summary, "detail": detail, "page": page, "quote": quote})

    for pid, url, lines in pages:
        for line in lines:
            if " · " in line and out["company_name"] is None and re.search(r"\d", line):
                out["company_name"] = line.split(" · ")[0].strip()
                tail = line.split(" · ", 1)[1]
                country = tail.rsplit(",", 1)[-1].strip().lower()
                if country in COUNTRY_CODES:
                    fact("country", COUNTRY_CODES[country], pid, tail.strip())
            for sentence in _sentences(line):
                low = sentence.lower()
                if m := re.search(r"\b(?:is an? |provides )(.+?)(?: company headquartered| to customers)", sentence):
                    industry = m.group(1).strip()
                    fact("industry", industry, pid, sentence)
                    for pattern, segment in SEGMENT_RULES:
                        if re.search(pattern, industry, re.I):
                            fact("segment", segment, pid, sentence)
                            break
                if m := re.search(r"[Hh]eadquartered in ([A-Z][\w .'-]+?, [A-Z][\w .'-]+?|[A-Z][\w'-]+)(?:,| serving|\.)", sentence):
                    hq_city = m.group(1).split(",")[0]
                    fact("hq", m.group(1), pid, sentence)
                if m := re.search(r"(?:serving|across) ([A-Z][\w ,'-]+?)\.$", sentence):
                    fact("served_regions", m.group(1), pid, sentence)
                if m := re.search(r"(\d[\d,]*)\s+(?:people|employees)", sentence):
                    fact("employees", m.group(1).replace(",", ""), pid, sentence)
                if m := re.search(r"(?:Founded in|Since) (\d{4})", sentence):
                    fact("founded", m.group(1), pid, sentence)
                if "rental bikes" not in low and (
                    m := re.search(r"fleet of (\d[\d,]*)\b|\bOur (\d[\d,]*) .+? are on the road", sentence)
                ):
                    fact("fleet_size", (m.group(1) or m.group(2)).replace(",", ""), pid, sentence)
                if re.search(r"\b(raised|secured|closed a|received a growth investment|credit facility|growth financing)\b", sentence) and not re.search(r"fund at|raised money for", low):
                    signal("funding", sentence, None, pid, sentence)
                elif re.search(r"\b(opened|began serving|started next-day delivery|acquired|acquisition|won the|added \d+ lockers)\b", sentence):
                    signal("expansion", sentence, None, pid, sentence)
                elif m := re.search(r"^(.+?) (?:has joined|joined|took over as|was named|was appointed|became)\b|appointed (.+?) as\b", sentence):
                    person = re.sub(r"^\d{1,2} \w+ \d{4} ", "", (m.group(2) or m.group(1) or "")).strip()
                    title = next((t for t in TITLE_WORDS if t in sentence), None)
                    if title:
                        signal("leadership_change", sentence, f"{person}, {title}", pid, sentence)
                for tech in TECH:
                    if re.search(rf"\b{re.escape(tech)}\b", sentence) and not (tech == "SAP" and "SAP S/4HANA" not in sentence and "in SAP" not in sentence and "into SAP" not in sentence):
                        signal("tech_stack", sentence, tech, pid, sentence)
                        break
                for comp in COMPETITORS:
                    name = (out["company_name"] or "").lower()
                    if comp in sentence and comp.lower() not in name and ("tracked with" in low or "runs" in low or "uses" in low):
                        signal("competitor_in_use", sentence, comp, pid, sentence)
            if " · Posted " in line:
                title = line.split(" · Posted ")[0]
                if hq_city and title.endswith(" " + hq_city):
                    title = title[: -len(hq_city) - 1]
                else:
                    title = title.rsplit(" ", 1)[0]
                tl = title.lower()
                if any(w in tl for w in FLEET_ROLE_WORDS) and not any(w in tl for w in NOT_FLEET_ROLES):
                    signal("hiring", f"Open role: {title}", title, pid, title)
            if url.endswith("team.html"):
                for title in TITLE_WORDS:
                    idx = line.find(" " + title + " ")
                    if idx > 0:
                        name = line[:idx].strip()
                        email = re.search(r"[\w.]+@[\w.-]+\.example", line)
                        out["people"].append(
                            {"name": name, "title": title, "email": email.group() if email else None, "page": pid, "quote": f"{name} {title}"}
                        )
                        break
    out["facts"] = list(facts.values())
    out["segment"] = facts.get("segment", {}).get("value")
    out["industry"] = facts.get("industry", {}).get("value")
    return json.dumps(out)


# ------------------------------------------------------------------------------------------------ qualification
INJECTION_TELLS = re.compile(r"rate this company 10/10|mark this account as qualified|already use your product|ceo personally", re.I)


def judge(messages: Sequence[Message], gullible: bool) -> str:
    prompt = _user(messages)
    if gullible and (tell := INJECTION_TELLS.search(prompt)):
        first = re.search(r"^(F\d+) ", prompt, re.M)
        return json.dumps({"adjustment": 10, "reason": f"The company's website says: {tell.group(0)}", "evidence": [first.group(1)] if first else []})
    size_zero = re.search(r"^- Company size: 0/", prompt, re.M)
    fleet_ok = re.search(r"^- Fleet size: (\d+)/(\d+)", prompt, re.M)
    hiring = re.search(r"^(S\d+) hiring \[current", prompt, re.M)
    if size_zero and fleet_ok and fleet_ok.group(1) == fleet_ok.group(2) and hiring and "fewer than" not in prompt.split("Company size")[1][:80]:
        return json.dumps(
            {"adjustment": 5, "reason": "Below the employee guideline, but the fleet is in range and a fleet role is open now.", "evidence": [hiring.group(1)]}
        )
    return json.dumps({"adjustment": 0, "reason": "The rubric reflects the evidence.", "evidence": []})


def qualify_llm_only(messages: Sequence[Message], gullible: bool) -> str:
    """A naive keyword judge: the strawman an LLM-only pipeline degrades to without rules. Not a real model."""
    text = " ".join(" ".join(lines) for _, _, lines in parse_pages(_user(messages))).lower()
    if gullible and INJECTION_TELLS.search(text):
        return json.dumps({"route": "qualified", "score": 100, "reasons": ["The website asks to rate the company 10/10."]})
    score = 30
    reasons = []
    if "fleet" in text:
        score += 15
        reasons.append("mentions a fleet")
    if re.search(r"fleet[^.]{0,40}posted|dispatcher|transport manager|telematics", text):
        score += 15
        reasons.append("hiring for fleet roles")
    if re.search(r"raised|secured|investment|fund", text):
        score += 10
        reasons.append("funding news")
    if re.search(r"opened|acquired|began serving|expan", text):
        score += 10
        reasons.append("expansion news")
    route = "qualified" if score >= 60 else "nurture" if score >= 35 else "disqualified"
    return json.dumps({"route": route, "score": score, "reasons": reasons})


# ------------------------------------------------------------------------------------------------ drafting
_EVIDENCE = re.compile(r'^(F\d+|S\d+) (\S+)(?: = | \[(current|stale)[^\]]*\] )(.*?) \| "(.*)" \(', re.M)
_OFFER = re.compile(r"^((?:VP|PP):\S+) (.*)$", re.M)
STOP = frozenset(
    "that this with your their have from about into they them were been will would could should what when also just more most than then only over some such very noticed read running means team teams".split()
)


def _evidence(prompt: str) -> dict[str, dict[str, str]]:
    items: dict[str, dict[str, str]] = {}
    for m in _EVIDENCE.finditer(prompt):
        items[m.group(1)] = {"kind": m.group(2), "state": m.group(3) or "", "value": m.group(4).strip(), "quote": m.group(5)}
    return items


def _sloppy(company: str) -> int:
    return sum(map(ord, company)) % 3


def draft(messages: Sequence[Message], gullible: bool) -> str:
    """Template drafts from the evidence. On a first attempt, one account in three gets an embellished sentence (an
    invented number or an unsupported congratulation), the way a careless model writes, so the claim checker and the
    regeneration loop have something to catch offline."""
    prompt = _user(messages)
    system = _system(messages)
    ev = _evidence(prompt)
    offer = dict(_OFFER.findall(prompt))
    contact = re.search(r"^Contact: ([^,]+), (.+)$", prompt, re.M)
    company = re.search(r"^Company: (.+?) \(", prompt, re.M)
    first = contact.group(1).split()[0] if contact else "there"
    name = company.group(1) if company else "your team"
    sender = re.search(r'Sign off with "([^"]+)"', system)
    sign = sender.group(1) if sender else "Dana"
    retry = "rejected by the fact checker" in prompt
    wanted = re.findall(r"^Variant ([A-C]):", system, re.M) or ["A"]
    if retry:
        only = re.search(r"Return only variant ([A-C])", prompt)
        wanted = [only.group(1)] if only else wanted[:1]
    current = [k for k, v in ev.items() if k.startswith("S") and v["state"] == "current" and v["kind"] != "competitor_in_use"]
    order = {"hiring": 0, "expansion": 1, "funding": 2, "leadership_change": 3, "tech_stack": 4}
    current.sort(key=lambda k: order.get(ev[k]["kind"], 9))
    fleet = next((k for k, v in ev.items() if v["kind"] == "fleet_size"), None)
    served = next((k for k, v in ev.items() if v["kind"] == "served_regions"), None)
    vp_for = {"hiring": "VP:safety", "leadership_change": "VP:safety", "expansion": "VP:rollout", "funding": "VP:fuel", "tech_stack": "VP:integration"}
    cta = "Would you be open to a 20-minute call to compare notes on your fleet?"

    def opener(sid: str) -> tuple[str, list[str]]:
        item = ev[sid]
        kind, value = item["kind"], item["value"]
        if kind == "hiring":
            return f"I noticed {name} is hiring a {value}.", [sid]
        if kind == "leadership_change":
            person, _, title = value.partition(", ")
            return f"I saw that {person} joined {name} as {title}.", [sid]
        if kind == "tech_stack":
            return f"I saw that your team works in {value}.", [sid]
        return f'I read your news: "{item["quote"].rstrip(".")}".', [sid]

    variants = []
    for v in wanted:
        claims: list[dict[str, Any]] = []
        sentences: list[str] = []
        if v == "A" and current:
            text, evid = opener(current[0])
            sentences.append(text)
            claims.append({"text": text, "evidence": evid})
            vp = vp_for.get(ev[current[0]]["kind"], "VP:safety")
        else:
            vp = "VP:fuel"
            if fleet:
                text = f"Running {ev[fleet]['value']} vehicles means small changes in idling and harsh braking add up quickly."
                sentences.append(text)
                claims.append({"text": f"Running {ev[fleet]['value']} vehicles", "evidence": [fleet]})
            else:
                sentences.append(f"Teams like yours at {name} often find that fuel and safety costs are hard to see per vehicle.")
        if vp in offer:
            text = f"Wayline Fleet offers {offer[vp][0].lower() + offer[vp][1:]}"
            sentences.append(text)
            claims.append({"text": text, "evidence": [vp]})
        if not retry and _sloppy(name) == 0 and fleet:
            sentences.append("Congrats on doubling the fleet this year.")
            claims.append({"text": "Congrats on doubling the fleet this year.", "evidence": [fleet]})
        elif not retry and _sloppy(name) == 1:
            sentences.append("Your 25 new depots must keep the team busy.")
            claims.append({"text": "Your 25 new depots", "evidence": [fleet or "F1"]})
        sentences.append("Most teams start by comparing one depot for a month, so the numbers come from your own vehicles.")
        sentences.append(cta)
        body1 = f"Hi {first},\n\n" + " ".join(sentences) + f"\n\n{sign}"
        pp = offer.get("PP:northbeam", "")
        body2 = (
            f"Hi {first},\n\nA quick follow-up. {pp} Happy to share how they rolled it out if it is useful for {name}.\n\n{sign}"
        )
        body3 = f"Hi {first},\n\nI will close the loop here. If fleet safety or fuel costs come up later, I am happy to talk.\n\n{sign}"
        emails = [
            {"step": 1, "subject": f"{name} and fleet safety" if v == "A" else "Fuel and safety per vehicle", "body": body1, "claims": claims},
            {"step": 2, "subject": "How Northbeam cut idle time", "body": body2, "claims": [{"text": pp, "evidence": ["PP:northbeam"]}] if pp else []},
            {"step": 3, "subject": "Closing the loop", "body": body3, "claims": []},
        ]
        variants.append({"variant": v, "angle": "signal-led" if v == "A" else "problem-led", "emails": emails})
    return json.dumps({"variants": variants})


def _content_words(text: str) -> set[str]:
    """Lower-case content words and numbers, ignoring capitalized names (the company and the contact)."""
    words = {w.lower() for w in re.findall(r"(?<![A-Za-z])[a-z][a-z'-]{3,}|\d[\d,]*", text) if w.lower() not in STOP}
    return {w.replace(",", "") for w in words}


def verify_claims(messages: Sequence[Message], gullible: bool) -> str:
    prompt = _user(messages)
    evidence_block = prompt.split("\n\nEmails:\n", 1)[0]
    texts = dict(re.findall(r"^(\S+): (.*)$", evidence_block, re.M))
    verdicts = []
    for cid, text, cites in re.findall(r'^\s+(C\d+): "(.*)" cites (.*)$', prompt, re.M):
        cited = " ".join(texts.get(c.strip(), "") for c in cites.split(","))
        words = _content_words(text)
        overlap = len(words & _content_words(cited)) / max(1, len(words))
        ok = overlap >= 0.4 and not re.search(r"doubl|congrat", text, re.I)
        verdicts.append({"id": cid, "verdict": "supported" if ok else "unsupported", "reason": f"word overlap with the cited evidence {overlap:.0%}"})
    return json.dumps({"claims": verdicts, "uncovered": []})


HANDLERS: dict[str, FakeHandler] = {
    "extract": extract,
    "judge": judge,
    "qualify_llm_only": qualify_llm_only,
    "draft": draft,
    "verify_claims": verify_claims,
}
