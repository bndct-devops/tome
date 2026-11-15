"""Monthly stats axes step real calendar months, not 30 days.

Stepping back 30 days from the 31st lands twice in the same month and skips
February, so library growth counted one month twice and dropped another.
"""
from datetime import date, datetime

import backend.api.stats as stats_api
from backend.api.stats import _last_months


def test_last_months_from_the_31st():
    months = _last_months(date(2026, 3, 31), 4)
    assert [m.strftime("%Y-%m") for m in months] == ["2025-12", "2026-01", "2026-02", "2026-03"]


def test_last_months_across_february_and_year():
    months = _last_months(datetime(2024, 2, 29, 23, 0), 13)
    keys = [m.strftime("%Y-%m") for m in months]
    assert keys[0] == "2023-02" and keys[-1] == "2024-02"
    assert len(set(keys)) == 13


class _Mar31(datetime):
    @classmethod
    def utcnow(cls):
        return cls(2026, 3, 31, 12, 0)


def test_stats_month_axes_on_the_31st(client, db, make_book, monkeypatch):
    book = make_book(title="February Arrival")
    book.added_at = datetime(2026, 2, 10, 12, 0)
    db.flush()
    monkeypatch.setattr(stats_api, "datetime", _Mar31)

    s = client.get("/api/stats?days=0").json()

    growth = s["library_growth"]
    keys = [g["month"] for g in growth]
    assert len(keys) == 24 and len(set(keys)) == 24
    assert keys[-1] == "2026-03" and keys[-2] == "2026-02"
    assert growth[-2]["total"] == 1 and growth[-1]["total"] == 1

    for series in (s["monthly_comparison"], s["genre_over_time"]):
        mk = [m["month"] for m in series]
        assert len(mk) == 12 and len(set(mk)) == 12
        assert mk[-1] == "2026-03" and mk[-2] == "2026-02"
