#!/usr/bin/env python3
"""Start/end time fields for Google Calendar events — all-day OR timed.

A date-only value (`YYYY-MM-DD`) means an all-day event and is emitted as
`{"date": ...}`; a full datetime is emitted as `{"dateTime": ...}`. Google's
all-day `end.date` is EXCLUSIVE, so callers pass the human-meaning INCLUSIVE last
day and this module stores the day after. Shared by the create/update scripts so
both handle all-day identically.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CalendarTimeError(ValueError):
    """Raised when start/end time specifications are inconsistent."""


def is_date_only(value: str) -> bool:
    """True when the value is a bare calendar date (all-day), not a datetime."""
    return bool(_DATE_ONLY.match(value.strip()))


def _parse_date(value: str) -> date:
    """Parse an all-day date value, converting the well-formed-but-invalid case
    (e.g. ``2026-13-45``: passes the YYYY-MM-DD regex but is not a real date) into
    a :class:`CalendarTimeError`. Callers catch that type; a raw ``ValueError`` from
    ``date.fromisoformat`` would escape create_event's handler and crash."""
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise CalendarTimeError(f"invalid calendar date {value!r}: {exc}") from exc


def start_field(value: str) -> dict:
    """Event start field: {'date': d} for all-day, else {'dateTime': v}."""
    if is_date_only(value):
        _parse_date(value)  # validate; raises CalendarTimeError on 2026-13-45 etc.
        return {"date": value.strip()}
    return {"dateTime": value}


def end_field(value: str) -> dict:
    """Event end field. For all-day, `value` is the INCLUSIVE last day and the
    stored end.date is that day + 1 (Google's exclusive end)."""
    if is_date_only(value):
        end_excl = (_parse_date(value) + timedelta(days=1)).isoformat()
        return {"date": end_excl}
    return {"dateTime": value}


def build_time_fields(start: str, end: str) -> tuple[dict, dict]:
    """Return (start_field, end_field), requiring both to be the same kind
    (both all-day dates or both timed datetimes)."""
    if is_date_only(start) != is_date_only(end):
        raise CalendarTimeError(
            "start and end must be the same kind: both all-day dates (YYYY-MM-DD) "
            f"or both timed datetimes. got start={start!r}, end={end!r}"
        )
    return start_field(start), end_field(end)
