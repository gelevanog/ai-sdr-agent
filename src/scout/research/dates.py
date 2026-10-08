"""Dates in page text: ISO dates, "14 July 2026", "July 14, 2026", "July 2026", "Q3 2026". Deterministic, so a
signal's age never depends on a model's arithmetic."""

from __future__ import annotations

import calendar
import datetime as dt
import re

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
_MONTHS["sept"] = 9
_M = r"(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?"
_ISO = re.compile(r"\b(20\d{2}|19\d{2})-(\d{2})-(\d{2})\b")
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_M}\s+(\d{{4}})\b", re.IGNORECASE)
_MDY = re.compile(rf"\b{_M}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE)
_MY = re.compile(rf"\b{_M}\s+(\d{{4}})\b", re.IGNORECASE)
_QY = re.compile(r"\bQ([1-4])\s+(\d{4})\b", re.IGNORECASE)


def _safe(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def find_dates(text: str) -> list[dt.date]:
    """All dates mentioned in the text, in order of appearance (month-only dates map to the 1st)."""
    found: list[tuple[int, dt.date]] = []
    taken: list[tuple[int, int]] = []

    def add(match: re.Match[str], value: dt.date | None) -> None:
        span = match.span()
        if value is None or any(span[0] < e and s < span[1] for s, e in taken):
            return
        taken.append(span)
        found.append((span[0], value))

    for m in _ISO.finditer(text):
        add(m, _safe(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in _DMY.finditer(text):
        add(m, _safe(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))))
    for m in _MDY.finditer(text):
        add(m, _safe(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))))
    for m in _MY.finditer(text):
        add(m, _safe(int(m.group(2)), _MONTHS[m.group(1).lower()], 1))
    for m in _QY.finditer(text):
        add(m, _safe(int(m.group(2)), (int(m.group(1)) - 1) * 3 + 1, 1))
    return [value for _, value in sorted(found, key=lambda item: item[0])]


def first_date(text: str) -> dt.date | None:
    dates = find_dates(text)
    return dates[0] if dates else None


def parse_iso(value: str | None) -> dt.date | None:
    if not value:
        return None
    m = _ISO.search(value)
    return _safe(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
