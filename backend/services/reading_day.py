"""The canonical "reading day" — the user's local calendar day with a 4-hour rollover.

A session started at 01:30 local time belongs to the previous evening's reading
day, so a bedtime read never splits across two days. Everything that buckets
reading activity by day — streaks, the daily chart, the activity heatmap,
re-read detection, per-book timelines, momentum, active-day sets — MUST go
through these helpers so the buckets can never drift apart. The one deliberate
exception is hour-of-day display (the hour × weekday heatmap): it uses the
plain timezone offset, because a 01:00 session should render at 1 AM, not 9 PM.

``tz_offset`` follows JS ``getTimezoneOffset()``: minutes, negative east of UTC
(CEST → -120).
"""
from datetime import date, datetime, timedelta

from sqlalchemy import func

ROLLOVER_HOURS = 4


def effective_hours(tz_offset_minutes: int, rollover_hours: int = ROLLOVER_HOURS) -> int:
    """Hours to add to a UTC timestamp so its date() is the reading day."""
    tz_hours = -(tz_offset_minutes // 60)
    return tz_hours - rollover_hours


def date_modifier(tz_offset_minutes: int, rollover_hours: int = ROLLOVER_HOURS) -> str:
    """SQLite modifier for DateTime columns: ``func.date(col, date_modifier(tz))``."""
    return f"{effective_hours(tz_offset_minutes, rollover_hours):+d} hours"


def epoch_day(column, tz_offset_minutes: int):
    """SQLAlchemy expression: epoch-seconds column (e.g. ``PageStat.start_time``)
    → its reading day as a 'YYYY-MM-DD' string."""
    return func.date(column, "unixepoch", date_modifier(tz_offset_minutes))


def epoch_day_int(epoch_seconds: int, tz_offset_minutes: int) -> int:
    """Python-side reading-day ordinal for an epoch timestamp — for set-based
    day grouping where only day *identity* matters, not the date string."""
    return (epoch_seconds + effective_hours(tz_offset_minutes) * 3600) // 86400


def effective_today(tz_offset_minutes: int, rollover_hours: int = ROLLOVER_HOURS) -> date:
    """The user's current reading day — what walking back a streak starts from."""
    return (datetime.utcnow() + timedelta(hours=effective_hours(tz_offset_minutes, rollover_hours))).date()


# ── DST-aware bucketing (DayCtx) ─────────────────────────────────────────────
#
# The plain-offset helpers above apply the client's *current* UTC offset to all
# of history, so in a DST timezone every session started between 3 and 4 AM
# local time in the opposite DST regime lands one day late (a 03:39 CET
# January read bucketed with the August CEST offset falls after the rollover
# boundary → wrong day, broken streak). DayCtx fixes that: given an IANA
# timezone name it emits per-row SQL CASE expressions over the zone's real
# transition instants, and does proper zoneinfo math on the Python side. With
# no (or an unknown) timezone name it reproduces the fixed-offset behaviour
# exactly, so older clients (plugin, OPDS, external API users) are unchanged.

from dataclasses import dataclass
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, literal

_TRANSITIONS_START_YEAR = 2000


def _offset_minutes_at(dt_utc: datetime, tz: ZoneInfo) -> int:
    off = dt_utc.astimezone(tz).utcoffset()
    return int(off.total_seconds() // 60) if off is not None else 0


@lru_cache(maxsize=16)
def _transitions(tz_name: str, end_year: int) -> tuple[tuple[int, int], ...]:
    """UTC-offset timeline for a zone: ((epoch_instant, offset_minutes), …),
    first entry anchored at _TRANSITIONS_START_YEAR. Probed at 12h steps and
    refined to the minute — exact for every real-world transition rule.
    Cache key includes end_year so the table refreshes at the year rollover."""
    tz = ZoneInfo(tz_name)
    from datetime import timezone as _tz
    t = datetime(_TRANSITIONS_START_YEAR, 1, 1, tzinfo=_tz.utc)
    end = datetime(end_year, 12, 31, tzinfo=_tz.utc)
    cur = _offset_minutes_at(t, tz)
    out = [(int(t.timestamp()), cur)]
    step = timedelta(hours=12)
    while t < end:
        nxt = t + step
        o = _offset_minutes_at(nxt, tz)
        if o != cur:
            lo, hi = t, nxt
            while hi - lo > timedelta(minutes=1):
                mid = lo + (hi - lo) / 2
                mid = mid.replace(second=0, microsecond=0)
                if mid <= lo:
                    break
                if _offset_minutes_at(mid, tz) == cur:
                    lo = mid
                else:
                    hi = mid
            out.append((int(hi.timestamp()), o))
            cur = o
        t = nxt
    return tuple(out)


def _valid_tz(name: str | None) -> str | None:
    if not name:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name


@dataclass(frozen=True)
class DayCtx:
    """Per-request bucketing context: the client's current offset (fallback,
    JS getTimezoneOffset semantics) plus an optional IANA timezone name that
    unlocks DST-correct per-row bucketing. Build one at the endpoint and pass
    it down — every day/hour/month bucket in a request must come from the same
    ctx or buckets drift apart."""

    tz_offset_minutes: int = 0
    tz_name: str | None = None
    rollover_hours: int = ROLLOVER_HOURS

    def __post_init__(self) -> None:
        object.__setattr__(self, "tz_name", _valid_tz(self.tz_name))

    # ── internals ────────────────────────────────────────────────────────────

    def _zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)  # type: ignore[arg-type]  (guarded by caller)

    def _trans(self) -> tuple[tuple[int, int], ...]:
        return _transitions(self.tz_name, datetime.utcnow().year + 2)

    def _case_modifier(self, boundary_exprs, shift_minutes: int):
        """SQL CASE picking the right '±N minutes' modifier per row.
        boundary_exprs: fn(epoch_instant) -> comparison expression."""
        trans = self._trans()
        whens = [
            (boundary_exprs(instant), literal(f"{off - shift_minutes:+d} minutes"))
            for instant, off in reversed(trans[1:])
        ]
        first_off = trans[0][1]
        return case(*whens, else_=literal(f"{first_off - shift_minutes:+d} minutes"))

    def _fixed_modifier(self, rollover_hours: int) -> str:
        return date_modifier(self.tz_offset_minutes, rollover_hours)

    @staticmethod
    def _dt_boundary(instant: int) -> str:
        return datetime.utcfromtimestamp(instant).strftime("%Y-%m-%d %H:%M:%S")

    # ── SQL expressions: reading day (local day with rollover) ───────────────

    def dt_day(self, column):
        """Reading day for a naive-UTC DateTime column → 'YYYY-MM-DD'."""
        if self.tz_name is None:
            return func.date(column, self._fixed_modifier(self.rollover_hours))
        mod = self._case_modifier(lambda i: column >= self._dt_boundary(i),
                                  self.rollover_hours * 60)
        return func.date(column, mod)

    def epoch_day(self, column):
        """Reading day for an epoch-seconds column → 'YYYY-MM-DD'."""
        if self.tz_name is None:
            return func.date(column, "unixepoch", self._fixed_modifier(self.rollover_hours))
        mod = self._case_modifier(lambda i: column >= i, self.rollover_hours * 60)
        return func.date(column, "unixepoch", mod)

    # ── SQL expressions: shifted local datetime ──────────────────────────────
    # With rollover (day-consistent months etc.) and without (hour-of-day
    # display, where a 1 AM session must read as hour 1).

    def dt_shifted(self, column, *, rollover: bool = True):
        shift = self.rollover_hours * 60 if rollover else 0
        if self.tz_name is None:
            return func.datetime(column, self._fixed_modifier(self.rollover_hours if rollover else 0))
        return func.datetime(column, self._case_modifier(
            lambda i: column >= self._dt_boundary(i), shift))

    def epoch_shifted(self, column, *, rollover: bool = True):
        shift = self.rollover_hours * 60 if rollover else 0
        if self.tz_name is None:
            return func.datetime(column, "unixepoch",
                                 self._fixed_modifier(self.rollover_hours if rollover else 0))
        return func.datetime(column, "unixepoch", self._case_modifier(
            lambda i: column >= i, shift))

    # ── Python side ──────────────────────────────────────────────────────────

    def py_day(self, epoch_seconds: int) -> date:
        """Reading day of an epoch timestamp."""
        if self.tz_name is None:
            eff = effective_hours(self.tz_offset_minutes, self.rollover_hours)
            return (datetime.utcfromtimestamp(epoch_seconds) + timedelta(hours=eff)).date()
        from datetime import timezone as _tz
        local = datetime.fromtimestamp(epoch_seconds, _tz.utc).astimezone(self._zone())
        return (local - timedelta(hours=self.rollover_hours)).date()

    def today(self) -> date:
        """The user's current reading day."""
        if self.tz_name is None:
            return effective_today(self.tz_offset_minutes, self.rollover_hours)
        from datetime import timezone as _tz
        now_local = datetime.now(_tz.utc).astimezone(self._zone())
        return (now_local - timedelta(hours=self.rollover_hours)).date()
