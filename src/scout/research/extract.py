"""The extraction prompt, and the deterministic checks applied to whatever the model returns.

The model proposes facts, signals and people, each with a page id and a quote. Scout keeps an item only if:
  * the quote is on the cited page (or, re-attributed, on another crawled page) after normalizing whitespace,
    quotes, dashes and case;
  * a numeric value (employees, fleet size, founding year) appears in its own quote;
  * an email address is printed on the page (Scout never guesses addresses);
  * the segment is one of the configured segment names.
Everything else is recorded in `rejected`, which is how the evaluation counts hallucinated facts.

Signal dates come from the page, not from the model: the date printed in the block that contains the quote (job
"Posted ...", an article's date), else a date inside the quote, else the page's "last updated" date. Freshness is
then a subtraction against the ICP's window for that signal type.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from scout.icp import Config
from scout.models import (
    FACT_FIELDS,
    SIGNAL_TYPES,
    Citation,
    Fact,
    Person,
    Rejected,
    Signal,
)
from scout.research.dates import first_date
from scout.research.html_text import ParsedPage
from scout.research.injection import SPOTLIGHT_NOTE, spotlight

EXTRACT_SYSTEM = """SCOUT_TASK: extract
You research companies for a B2B sales team. From the website pages below, extract a company profile as JSON.

{spotlight_note}

Rules:
- Use only what the pages say. Do not guess, infer, calculate or use outside knowledge.
- Every item needs "page" (the PAGE id, e.g. "P2") and "quote": words copied exactly from one block of that page
  (at most 30 words, no ellipses, no paraphrase, without the [P2.3] block prefix). The quote must contain the value.
- facts: at most one per field, only when a page states it:
  segment (choose one of the segment names below; quote the sentence that says what the company does),
  industry (a few words), employees (a number), fleet_size (number of motor vehicles the company itself operates;
  not bicycles, not customers' vehicles), hq (city), country (ISO 3166 alpha-2 code), founded (year),
  served_regions.
- signals (buying signals for {seller}: {seller_one_liner}):
  hiring: an open job for a fleet, dispatch, transport, fleet-safety or telematics role (not drivers, technicians,
    couriers or office roles); detail = the job title.
  funding: the company itself raised money or received an investment or credit facility (not a fund it manages).
  expansion: a new depot, branch, region, route network, contract or acquisition.
  leadership_change: a new executive (CEO, COO, operations, fleet); detail = "Name, Title".
  tech_stack: business software the company uses (dispatch, ERP, field service); detail = the product name.
  competitor_in_use: the company already uses a fleet telematics or GPS tracking product; detail = the product.
  Include old signals as well; Scout checks their dates itself. Do not invent signals the pages do not state.
- people: names and job titles listed on team or leadership pages; "email" only if the address is printed, else null.

Segment names: {segments}

Reply with one JSON object and nothing else:
{{"company_name": "...", "segment": "...", "industry": "...",
 "facts": [{{"field": "...", "value": "...", "page": "P1", "quote": "..."}}],
 "signals": [{{"type": "...", "summary": "...", "detail": "...", "page": "P3", "quote": "..."}}],
 "people": [{{"name": "...", "title": "...", "email": null, "page": "P5", "quote": "..."}}]}}"""

RAW_NOTE = "The website pages follow."


def build_extract_messages(
    domain_url: str, pages: list[tuple[str, ParsedPage]], config: Config, *, spotlighting: bool = True
) -> list[dict[str, str]]:
    system = EXTRACT_SYSTEM.format(
        spotlight_note=SPOTLIGHT_NOTE if spotlighting else RAW_NOTE,
        seller=config.seller.company,
        seller_one_liner=config.seller.one_liner,
        segments=", ".join(config.segments),
    )
    if spotlighting:
        body = "\n\n".join(spotlight(pid, page) for pid, page in pages)
    else:
        body = "\n\n".join(f"PAGE {pid} {page.url}\n{page.text}" for pid, page in pages)
    user = f"Company website: {domain_url}\n\n{body}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ------------------------------------------------------------------------------------------- validation helpers
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
_PREFIX = re.compile(r"^\s*(?:\[P\d+(?:\.\d+)?\]\s*)?(?:\(\d{4}-\d{2}-\d{2}\)\s*)?")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip().strip(".,;:!?\"' ")


def clean_quote(quote: str) -> str:
    return _PREFIX.sub("", quote.strip()).strip().strip('"').strip()


def numbers_in(text: str) -> set[int]:
    return {int(m.replace(",", "")) for m in re.findall(r"\d[\d,]*", text) if m.replace(",", "").isdigit()}


def parse_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"\d[\d,]*", str(value))
    return int(match.group().replace(",", "")) if match else None


@dataclass
class Located:
    page: ParsedPage
    block_index: int | None
    reattributed: bool


def locate(quote: str, cited: ParsedPage | None, pages: list[ParsedPage]) -> Located | None:
    """The page (and block) where the quote appears: the cited page first, then any other crawled page."""
    needle = normalize(quote)
    if len(needle) < 3:
        return None
    ordered = ([cited] if cited else []) + [p for p in pages if p is not cited]
    for page in ordered:
        for i, block in enumerate(page.blocks):
            if needle in normalize(block.text):
                return Located(page, i, page is not cited)
        if needle in normalize(page.text):
            return Located(page, None, page is not cited)
    return None


def quote_on_page(quote: str, page_text: str) -> bool:
    """Citation validity as the evaluation measures it: is the quote on the cited page?"""
    needle = normalize(clean_quote(quote))
    return len(needle) >= 3 and needle in normalize(page_text)


# ------------------------------------------------------------------------------------------- assembling the profile
@dataclass
class Extracted:
    name: str | None
    segment: str | None
    industry: str | None
    facts: list[Fact]
    signals: list[Signal]
    people: list[Person]
    rejected: list[Rejected]


def _date_for(located: Located, quote: str) -> tuple[dt.date | None, str]:
    if located.block_index is not None:
        block = located.page.blocks[located.block_index]
        if block.date:
            return block.date, "block"
    in_quote = first_date(quote)
    if in_quote:
        return in_quote, "quote"
    if located.page.page_date:
        return located.page.page_date, "page"
    return None, "none"


def validate_extraction(
    data: dict[str, Any],
    page_ids: dict[str, ParsedPage],
    config: Config,
    *,
    today: dt.date,
    keep_people: bool = True,
) -> Extracted:
    pages = list(page_ids.values())
    rejected: list[Rejected] = []
    facts: list[Fact] = []
    signals: list[Signal] = []
    people: list[Person] = []

    def resolve(item: dict[str, Any], kind: str, value: str) -> tuple[Citation, Located] | None:
        quote = clean_quote(str(item.get("quote") or ""))
        cited = page_ids.get(str(item.get("page") or "").strip())
        url = cited.url if cited else str(item.get("page") or "")
        if not quote:
            rejected.append(Rejected(kind=kind, value=value, url=url, quote="", reason="no quote"))  # type: ignore[arg-type]
            return None
        located = locate(quote, cited, pages)
        if located is None:
            rejected.append(Rejected(kind=kind, value=value, url=url, quote=quote, reason="quote not on any crawled page"))  # type: ignore[arg-type]
            return None
        date, _ = _date_for(located, quote)
        citation = Citation(url=located.page.url, quote=quote, date=date, page_date=located.page.page_date)
        return citation, located

    seen_fields: set[str] = set()
    for item in data.get("facts") or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        value = str(item.get("value") if item.get("value") is not None else "").strip()
        if field not in FACT_FIELDS or not value or field in seen_fields:
            continue
        resolved = resolve(item, "fact", f"{field}={value}")
        if resolved is None:
            continue
        citation, _ = resolved
        number: int | None = None
        if field in {"employees", "fleet_size", "founded"}:
            number = parse_number(value)
            if number is None or number not in numbers_in(citation.quote):
                rejected.append(
                    Rejected(kind="fact", value=f"{field}={value}", url=citation.url, quote=citation.quote, reason="number not in the quote")
                )
                continue
        if field == "segment" and value not in config.segments:
            rejected.append(Rejected(kind="fact", value=f"segment={value}", url=citation.url, quote=citation.quote, reason="unknown segment"))
            continue
        if field == "country":
            value = value.upper()[:2]
        seen_fields.add(field)
        facts.append(Fact(id=f"F{len(facts) + 1}", field=field, value=value, number=number, citation=citation))  # type: ignore[arg-type]

    segment = next((f.value for f in facts if f.field == "segment"), None)
    if segment is None:
        raw_segment = str(data.get("segment") or "").strip()
        segment = raw_segment if raw_segment in config.segments else None
    industry_fact = next((f.value for f in facts if f.field == "industry"), None)
    industry = industry_fact or (str(data.get("industry") or "").strip() or None)

    windows = config.icp.signals.windows_days
    seen_signals: set[tuple[str, str]] = set()
    for item in data.get("signals") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip()
        if kind not in SIGNAL_TYPES:
            continue
        detail = str(item.get("detail") or "").strip() or None
        summary = str(item.get("summary") or detail or kind).strip()
        resolved = resolve(item, "signal", f"{kind}: {detail or summary}")
        if resolved is None:
            continue
        citation, located = resolved
        key = (kind, normalize(detail or citation.quote))
        if key in seen_signals:
            continue
        seen_signals.add(key)
        date, source = _date_for(located, citation.quote)
        age = (today - date).days if date else None
        window = windows.get(kind, 365)
        current = age is not None and -31 <= age <= window
        signals.append(
            Signal(
                id=f"S{len(signals) + 1}",
                type=kind,  # type: ignore[arg-type]
                summary=summary[:300],
                detail=detail,
                date=date,
                date_source=source,  # type: ignore[arg-type]
                age_days=age,
                current=current,
                citation=citation,
            )
        )

    if keep_people:
        seen_people: set[str] = set()
        for item in data.get("people") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            title = str(item.get("title") or "").strip()
            if not name or not title or normalize(name) in seen_people:
                continue
            resolved = resolve(item, "person", f"{name}, {title}")
            if resolved is None:
                continue
            citation, located = resolved
            if normalize(name) not in normalize(citation.quote) and normalize(name) not in normalize(located.page.text):
                rejected.append(Rejected(kind="person", value=name, url=citation.url, quote=citation.quote, reason="name not on the page"))
                continue
            email_raw = item.get("email")
            email = str(email_raw).strip().lower() if email_raw else None
            if email and (not _EMAIL.fullmatch(email) or email not in located.page.text.lower()):
                rejected.append(
                    Rejected(kind="person", value=f"{name} <{email}>", url=citation.url, quote=citation.quote, reason="email not printed on the page")
                )
                email = None
            seen_people.add(normalize(name))
            people.append(Person(name=name, title=title, email=email, citation=citation))

    name = str(data.get("company_name") or "").strip() or None
    return Extracted(name=name, segment=segment, industry=industry, facts=facts, signals=signals, people=people, rejected=rejected)
