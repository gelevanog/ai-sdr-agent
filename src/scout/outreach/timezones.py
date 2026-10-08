"""The prospect's time zone from the researched headquarters (state or province first, then the country)."""

from __future__ import annotations

_REGIONS = {
    # United States
    "alabama": "America/Chicago", "arizona": "America/Phoenix", "california": "America/Los_Angeles",
    "colorado": "America/Denver", "florida": "America/New_York", "georgia": "America/New_York",
    "idaho": "America/Boise", "illinois": "America/Chicago", "indiana": "America/Indiana/Indianapolis",
    "kansas": "America/Chicago", "kentucky": "America/New_York", "louisiana": "America/Chicago",
    "maine": "America/New_York", "massachusetts": "America/New_York", "michigan": "America/Detroit",
    "minnesota": "America/Chicago", "missouri": "America/Chicago", "nebraska": "America/Chicago",
    "nevada": "America/Los_Angeles", "new jersey": "America/New_York", "new york": "America/New_York",
    "north carolina": "America/New_York", "ohio": "America/New_York", "oregon": "America/Los_Angeles",
    "pennsylvania": "America/New_York", "tennessee": "America/Chicago", "texas": "America/Chicago",
    "utah": "America/Denver", "virginia": "America/New_York", "washington": "America/Los_Angeles",
    "wisconsin": "America/Chicago",
    # Canada
    "alberta": "America/Edmonton", "british columbia": "America/Vancouver", "ontario": "America/Toronto",
    "quebec": "America/Toronto", "saskatchewan": "America/Regina", "manitoba": "America/Winnipeg",
    "nova scotia": "America/Halifax",
}  # fmt: skip

_COUNTRIES = {
    "US": "America/New_York", "CA": "America/Toronto", "GB": "Europe/London", "IE": "Europe/Dublin",
    "DE": "Europe/Berlin", "AT": "Europe/Vienna", "NL": "Europe/Amsterdam", "BE": "Europe/Brussels",
    "FR": "Europe/Paris", "NO": "Europe/Oslo", "SE": "Europe/Stockholm", "DK": "Europe/Copenhagen",
    "FI": "Europe/Helsinki", "AU": "Australia/Sydney", "JP": "Asia/Tokyo", "PE": "America/Lima",
}  # fmt: skip


def guess_timezone(hq: str | None, country: str | None) -> str:
    low = (hq or "").lower()
    for region, tz in _REGIONS.items():
        if region in low:
            return tz
    return _COUNTRIES.get((country or "").upper(), "UTC")
