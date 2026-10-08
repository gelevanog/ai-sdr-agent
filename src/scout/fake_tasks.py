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
                elif m := re.search(
                    r"^(.+?) (?:has joined .+? as|joined .+? as|took over as|was named|was appointed|became|has appointed (.+?) as)\s*(.+?)(?: of [A-Z].*?)?(?:,| on | in |\.|$)",
                    sentence,
                ):
                    person = (m.group(2) or m.group(1)).replace("Ridgeway has appointed ", "")
                    if any(t in sentence for t in ("Chief", "VP", "Head of", "Director", "Managing Director")):
                        signal("leadership_change", sentence, f"{person.strip()}, {m.group(3).strip()}", pid, sentence)
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


HANDLERS: dict[str, FakeHandler] = {"extract": extract}
