"""Usage ledger: record one row per AI call and summarise a month.

Prices are USD per million tokens, first-party list prices checked
2026-10-10. Keep them here, in one dict, so updating them is a one-line
change. Cost is computed at insert and stored, so old rows keep the price
they were billed at.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.ai_usage import AIUsage
from backend.models.user import User
from backend.services.ai import registry
from backend.services.ai.provider import AIResult

logger = logging.getLogger(__name__)

# model -> (input, output, cache read, cache write) per million tokens.
# Cache-write figures are estimates (1.25x input, the usual 5-minute-cache
# multiplier); the Haiku 5.5 cache figures are estimates as well.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    # Server-side fallback targets: a refused request can be answered by one
    # of these, and the response names it. Estimates on the same basis.
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25),
}


def _price_for(model: str) -> tuple[float, float, float, float]:
    if model in PRICES:
        return PRICES[model]
    # Unknown id (a newer fallback target, a dated alias): price it as the
    # family it belongs to so the meter never reads zero for real spend.
    for family, ref in (("opus", "claude-opus-5-5"), ("sonnet", "claude-sonnet-5-5"),
                        ("haiku", "claude-haiku-5-5")):
        if family in model:
            return PRICES[ref]
    logger.warning("No price for AI model %s; pricing it as Opus", model)
    return PRICES["claude-opus-5-5"]


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    p_in, p_out, p_cr, p_cw = _price_for(model)
    total = (input_tokens * p_in + output_tokens * p_out
             + cache_read_tokens * p_cr + cache_write_tokens * p_cw) / 1_000_000
    return round(total, 6)


def record_usage(db: Session, *, user_id: int | None, feature: str, key_source: str,
                 result: AIResult) -> AIUsage:
    row = AIUsage(
        user_id=user_id,
        feature=feature,
        model=result.model,
        key_source=key_source,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_write_tokens=result.cache_write_tokens,
        cost_usd=cost_usd(result.model, result.input_tokens, result.output_tokens,
                          result.cache_read_tokens, result.cache_write_tokens),
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return row


def parse_month(month: str | None) -> tuple[str, datetime, datetime]:
    """"YYYY-MM" (or None = current UTC month) -> (label, start, end). Raises ValueError."""
    if month:
        start = datetime.strptime(month, "%Y-%m")
    else:
        now = datetime.utcnow()
        start = datetime(now.year, now.month, 1)
    end = datetime(start.year + 1, 1, 1) if start.month == 12 else datetime(start.year, start.month + 1, 1)
    return start.strftime("%Y-%m"), start, end


def _totals(row: Any) -> dict[str, Any]:
    return {
        "calls": int(row.calls or 0),
        "input_tokens": int(row.input_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "cost_usd": round(float(row.cost_usd or 0.0), 4),
    }


def _agg_columns():
    return (
        func.count(AIUsage.id).label("calls"),
        func.sum(AIUsage.input_tokens + AIUsage.cache_read_tokens + AIUsage.cache_write_tokens).label("input_tokens"),
        func.sum(AIUsage.output_tokens).label("output_tokens"),
        func.sum(AIUsage.cost_usd).label("cost_usd"),
    )


def month_summary(db: Session, *, month: str | None, user_id: int | None,
                  all_users: bool = False) -> dict[str, Any]:
    """Totals for one month. ``user_id`` scopes to one user unless
    ``all_users`` is set, in which case a per-user breakdown is added."""
    label, start, end = parse_month(month)
    base = [AIUsage.created_at >= start, AIUsage.created_at < end]
    if not all_users:
        base.append(AIUsage.user_id == user_id)

    total = db.query(*_agg_columns()).filter(*base).one()
    by_feature_rows = (
        db.query(AIUsage.feature, *_agg_columns())
        .filter(*base)
        .group_by(AIUsage.feature)
        .all()
    )
    by_feature = []
    for r in sorted(by_feature_rows, key=lambda r: -(r.cost_usd or 0)):
        f = registry.get_feature(r.feature)
        by_feature.append({"feature": r.feature, "label": f.label if f else r.feature, **_totals(r)})

    out: dict[str, Any] = {
        "month": label,
        "scope": "all" if all_users else "self",
        "total": _totals(total),
        "by_feature": by_feature,
    }
    if all_users:
        by_user_rows = (
            db.query(AIUsage.user_id, User.username, *_agg_columns())
            .outerjoin(User, User.id == AIUsage.user_id)
            .filter(*base)
            .group_by(AIUsage.user_id, User.username)
            .all()
        )
        out["by_user"] = [
            {"user_id": r.user_id, "username": r.username, **_totals(r)}
            for r in sorted(by_user_rows, key=lambda r: -(r.cost_usd or 0))
        ]
    return out
