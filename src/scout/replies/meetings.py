"""Meeting slots: the first 30-minute slot on a weekday that sits inside the seller's meeting hours and the
prospect's business hours, from the next business day on, not overlapping a held meeting."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo


def find_slot(
    after: dt.datetime,
    *,
    seller_tz: str,
    seller_hours: tuple[int, int],
    prospect_tz: str,
    prospect_hours: tuple[int, int] = (9, 17),
    minutes: int = 30,
    busy: list[tuple[dt.datetime, dt.datetime]] | None = None,
    days: int = 14,
) -> tuple[dt.datetime, dt.datetime] | None:
    busy = busy or []
    seller = ZoneInfo(seller_tz)
    prospect = ZoneInfo(prospect_tz)
    start_day = after.astimezone(seller).date() + dt.timedelta(days=1)
    for offset in range(days):
        day = start_day + dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        slot = dt.datetime.combine(day, dt.time(seller_hours[0], 0), seller)
        close = dt.datetime.combine(day, dt.time(seller_hours[1], 0), seller)
        while slot + dt.timedelta(minutes=minutes) <= close:
            end = slot + dt.timedelta(minutes=minutes)
            local_start, local_end = slot.astimezone(prospect), end.astimezone(prospect)
            in_hours = (
                local_start.weekday() < 5
                and local_start.date() == local_end.date()
                and prospect_hours[0] <= local_start.hour
                and (
                    local_end.hour < prospect_hours[1]
                    or (local_end.hour == prospect_hours[1] and local_end.minute == 0)
                )
            )
            clash = any(slot < b_end and b_start < end for b_start, b_end in busy)
            if in_hours and not clash:
                return slot.astimezone(dt.UTC), end.astimezone(dt.UTC)
            slot = end
    return None
