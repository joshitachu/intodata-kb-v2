"""Internal ops-metrics endpoint for Mission Control heartbeat aggregation.

Called by the MCP server before each telemetry push. Returns user counts,
query activity, peak hours, and conversation stats from the RAG backend.
Auth: same Bearer token as MCP_INGEST_API_KEY.
"""

from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.usage import verify_ingest_api_key
from src.db.models.conversation import Conversation
from src.db.models.query_usage import QueryUsage
from src.db.models.user import User
from src.db.session import get_db

router = APIRouter(tags=["ops-metrics"])


@router.get(
    "/internal/ops-metrics",
    dependencies=[Depends(verify_ingest_api_key)],
)
async def get_ops_metrics(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    now = datetime.datetime.now(datetime.timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = now - datetime.timedelta(days=7)
    thirty_days_ago = now - datetime.timedelta(days=30)
    twenty_four_hours_ago = now - datetime.timedelta(hours=24)

    # ── User counts ───────────────────────────────────────────────────────────
    user_count = (await db.execute(select(func.count(User.id)))).scalar() or 0

    active_7d = (
        await db.execute(
            select(func.count(User.id)).where(User.last_login_at >= seven_days_ago)
        )
    ).scalar() or 0

    active_30d = (
        await db.execute(
            select(func.count(User.id)).where(User.last_login_at >= thirty_days_ago)
        )
    ).scalar() or 0

    # ── Query activity — last 7 days by date ─────────────────────────────────
    rows_7d = (
        await db.execute(
            select(
                func.date(QueryUsage.created_at).label("day"),
                func.count(QueryUsage.id).label("count"),
            )
            .where(QueryUsage.created_at >= seven_days_ago)
            .group_by(text("day"))
            .order_by(text("day"))
        )
    ).all()
    queries_7d = [{"date": str(r.day), "count": r.count} for r in rows_7d]

    # Fill gaps so the chart always has 7 points
    existing_dates = {r["date"] for r in queries_7d}
    full_7d: list[dict[str, Any]] = []
    for i in range(6, -1, -1):
        day = (now - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        full_7d.append({"date": day, "count": next((r["count"] for r in queries_7d if r["date"] == day), 0)})

    # ── Query activity — last 24h by hour ─────────────────────────────────────
    rows_24h = (
        await db.execute(
            select(
                func.extract("hour", QueryUsage.created_at).label("hour"),
                func.count(QueryUsage.id).label("count"),
            )
            .where(QueryUsage.created_at >= twenty_four_hours_ago)
            .group_by(text("hour"))
            .order_by(text("hour"))
        )
    ).all()
    hour_map = {int(r.hour): r.count for r in rows_24h}
    queries_24h = [{"hour": h, "count": hour_map.get(h, 0)} for h in range(24)]

    peak_hour = max(queries_24h, key=lambda x: x["count"])["hour"] if any(r["count"] for r in queries_24h) else None

    # ── Today's queries ───────────────────────────────────────────────────────
    queries_today = (
        await db.execute(
            select(func.count(QueryUsage.id)).where(QueryUsage.created_at >= today_start)
        )
    ).scalar() or 0

    total_queries = (await db.execute(select(func.count(QueryUsage.id)))).scalar() or 0

    # ── Conversations ─────────────────────────────────────────────────────────
    conversation_count = (await db.execute(select(func.count(Conversation.id)))).scalar() or 0

    avg_msgs = (
        (await db.execute(
            select(func.avg(
                select(func.count(QueryUsage.id))
                .where(QueryUsage.conversation_id == Conversation.id)
                .correlate(Conversation)
                .scalar_subquery()
            ))
        )).scalar()
    )

    return {
        "user_count": int(user_count),
        "active_users_7d": int(active_7d),
        "active_users_30d": int(active_30d),
        "queries_today": int(queries_today),
        "total_queries": int(total_queries),
        "queries_7d": full_7d,
        "queries_24h": queries_24h,
        "peak_hour": peak_hour,
        "conversation_count": int(conversation_count),
        "avg_queries_per_conversation": round(float(avg_msgs), 1) if avg_msgs else 0.0,
    }
