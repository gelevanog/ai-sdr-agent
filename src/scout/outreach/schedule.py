"""Sequence scheduling: each step goes out on a weekday, inside business hours, in the prospect's time zone.

Step 1 goes at the first business-hours slot after approval (plus a short buffer so a reviewer can still pull it
back); follow-ups go N business days after step 1. The minute within the hour is derived from the account, so
sends are spread out instead of all landing at 09:00 sharp.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from zoneinfo import ZoneInfo


def _is_business_day(day: dt.date) -> bool:
    return day.weekday() < 5


def add_business_days(day: dt.date, n: int) -> dt.date:
    current = day
    added = 0
    while added < n:
        current += dt.timedelta(days=1)
        if _is_business_day(current):
            added += 1
    return current


def next_business_slot(after: dt.datetime, tz: str, *, start_hour: int = 9, end_hour: int = 17, jitter_key: str = "") -> dt.datetime:
    """The first moment at or after `after` that is a weekday between start_hour and end_hour local time
    (returned in UTC). The jitter (0-45 minutes, stable per key) is added to the start of a business day."""
    if after.tzinfo is None:
        raise ValueError("`after` must be timezone-aware")
    zone = ZoneInfo(tz)
    local = after.astimezone(zone)
    jitter = int(hashlib.sha256(jitter_key.encode()).hexdigest(), 16) % 46 if jitter_key else 0
    for _ in range(15):
        day = local.date()
        opening = dt.datetime.combine(day, dt.time(start_hour, 0), zone) + dt.timedelta(minutes=jitter)
        closing = dt.datetime.combine(day, dt.time(end_hour, 0), zone)
        if _is_business_day(day):
            if local <= opening:
                return opening.astimezone(dt.UTC)
            if local < closing:
                return local.astimezone(dt.UTC)
        local = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0, 0), zone)
    raise RuntimeError("no business slot found")  # unreachable: a week always has a weekday


def plan_sequence(
    approved_at: dt.datetime,
    tz: str,
    *,
    followup_business_days: list[int],
    start_hour: int = 9,
    end_hour: int = 17,
    buffer_minutes: int = 15,
    key: str = "",
) -> list[dt.datetime]:
    first = next_business_slot(
        approved_at + dt.timedelta(minutes=buffer_minutes), tz, start_hour=start_hour, end_hour=end_hour, jitter_key=key
    )
    times = [first]
    zone = ZoneInfo(tz)
    first_local = first.astimezone(zone)
    for n in followup_business_days:
        day = add_business_days(first_local.date(), n)
        local = dt.datetime.combine(day, first_local.time(), zone)
        times.append(next_business_slot(local.astimezone(dt.UTC), tz, start_hour=start_hour, end_hour=end_hour, jitter_key=key))
    return times


def local_label(when: dt.datetime, tz: str) -> str:
    local = when.astimezone(ZoneInfo(tz))
    return local.strftime("%a %d %b %H:%M ") + (local.tzname() or tz)
