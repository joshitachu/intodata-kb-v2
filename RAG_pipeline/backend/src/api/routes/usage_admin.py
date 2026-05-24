"""Admin endpoints: usage analytics and token cost tracking."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy import func as sa_func

from src.api.dependencies import AuditViewer, DBSession
from src.db.models import QueryUsage, User

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/usage/summary", summary="Aggregated token usage summary")
async def usage_summary(
    admin: AuditViewer,
    db: DBSession,
    days: int = 30,
):
    """Aggregated token usage summary for the last N days."""
    import datetime

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    # Total tokens by interface
    interface_result = await db.execute(
        select(
            QueryUsage.interface,
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(QueryUsage.created_at >= cutoff)
        .group_by(QueryUsage.interface)
    )
    by_interface = [
        {
            "interface": row.interface,
            "count": row.count,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cost_usd": round(float(row.cost_usd), 4),
        }
        for row in interface_result
    ]

    # Total tokens by model
    model_result = await db.execute(
        select(
            QueryUsage.model_name,
            QueryUsage.provider,
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(QueryUsage.created_at >= cutoff)
        .group_by(QueryUsage.model_name, QueryUsage.provider)
    )
    by_model = [
        {
            "model": row.model_name,
            "provider": row.provider,
            "count": row.count,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cost_usd": round(float(row.cost_usd), 4),
        }
        for row in model_result
    ]

    # Daily totals
    daily_result = await db.execute(
        select(
            sa_func.date_trunc("day", QueryUsage.created_at).label("day"),
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(QueryUsage.created_at >= cutoff)
        .group_by("day")
        .order_by("day")
    )
    daily = [
        {
            "date": row.day.isoformat() if row.day else "",
            "count": row.count,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cost_usd": round(float(row.cost_usd), 4),
        }
        for row in daily_result
    ]

    return {
        "days": days,
        "by_interface": by_interface,
        "by_model": by_model,
        "daily": daily,
    }


@router.get("/usage/recent", summary="List recent query usage entries")
async def usage_recent(
    admin: AuditViewer,
    db: DBSession,
    skip: int = 0,
    limit: int = Query(default=50, ge=1, le=100),
    interface: str | None = None,
    user_id: int | None = None,
):
    """List recent query usage entries."""
    query = select(QueryUsage).order_by(QueryUsage.created_at.desc())
    if interface:
        query = query.where(QueryUsage.interface == interface)
    if user_id is not None:
        query = query.where(QueryUsage.user_id == user_id)

    result = await db.execute(query.offset(skip).limit(limit))
    entries = result.scalars().all()

    return {
        "entries": [
            {
                "id": e.id,
                "user_id": e.user_id,
                "interface": e.interface,
                "query_text": (e.query_text[:100] + "...") if e.query_text and len(e.query_text) > 100 else e.query_text,
                "model_name": e.model_name,
                "provider": e.provider,
                "input_tokens": e.input_tokens,
                "output_tokens": e.output_tokens,
                "cost_usd": e.cost_usd,
                "latency_ms": e.latency_ms,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ],
    }


@router.get("/usage/by-user", summary="Usage aggregated by user")
async def usage_by_user(
    admin: AuditViewer,
    db: DBSession,
    days: int = 30,
):
    """Aggregated usage grouped by user for the last N days."""
    import datetime

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    result = await db.execute(
        select(
            QueryUsage.user_id,
            User.username,
            User.email,
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
            sa_func.coalesce(sa_func.avg(QueryUsage.latency_ms), 0).label("avg_latency_ms"),
        )
        .join(User, QueryUsage.user_id == User.id, isouter=True)
        .where(QueryUsage.created_at >= cutoff)
        .group_by(QueryUsage.user_id, User.username, User.email)
        .order_by(sa_func.sum(QueryUsage.cost_usd).desc())
    )

    return {
        "days": days,
        "by_user": [
            {
                "user_id": row.user_id,
                "username": row.username or "(anonymous)",
                "email": row.email,
                "count": row.count,
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "cost_usd": round(float(row.cost_usd), 6),
                "avg_latency_ms": round(float(row.avg_latency_ms), 0),
            }
            for row in result
        ],
    }


@router.get("/usage/user/{user_id}", summary="Detailed usage breakdown for a single user")
async def usage_user_detail(
    user_id: int,
    admin: AuditViewer,
    db: DBSession,
    days: int = 30,
):
    """Detailed usage breakdown for a single user."""
    import datetime

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    # Fetch user info
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    base_filter = (QueryUsage.created_at >= cutoff) & (QueryUsage.user_id == user_id)

    # Totals
    totals_result = await db.execute(
        select(
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
            sa_func.coalesce(sa_func.avg(QueryUsage.latency_ms), 0).label("avg_latency_ms"),
        ).where(base_filter)
    )
    totals_row = totals_result.one()

    # Daily breakdown
    daily_result = await db.execute(
        select(
            sa_func.date_trunc("day", QueryUsage.created_at).label("day"),
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(base_filter)
        .group_by("day")
        .order_by("day")
    )

    # By interface
    interface_result = await db.execute(
        select(
            QueryUsage.interface,
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(base_filter)
        .group_by(QueryUsage.interface)
    )

    # By model
    model_result = await db.execute(
        select(
            QueryUsage.model_name,
            QueryUsage.provider,
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
        )
        .where(base_filter)
        .group_by(QueryUsage.model_name, QueryUsage.provider)
    )

    return {
        "user_id": user_id,
        "username": user.username,
        "email": user.email,
        "days": days,
        "totals": {
            "count": totals_row.count,
            "input_tokens": int(totals_row.input_tokens),
            "output_tokens": int(totals_row.output_tokens),
            "cost_usd": round(float(totals_row.cost_usd), 6),
            "avg_latency_ms": round(float(totals_row.avg_latency_ms), 0),
        },
        "daily": [
            {
                "date": row.day.isoformat() if row.day else "",
                "count": row.count,
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "cost_usd": round(float(row.cost_usd), 4),
            }
            for row in daily_result
        ],
        "by_interface": [
            {
                "interface": row.interface,
                "count": row.count,
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "cost_usd": round(float(row.cost_usd), 4),
            }
            for row in interface_result
        ],
        "by_model": [
            {
                "model": row.model_name,
                "provider": row.provider,
                "count": row.count,
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "cost_usd": round(float(row.cost_usd), 4),
            }
            for row in model_result
        ],
    }


@router.get("/usage/daily-by-user", summary="Daily usage per top N users for stacked charts")
async def usage_daily_by_user(
    admin: AuditViewer,
    db: DBSession,
    days: int = 30,
    top: int = 10,
):
    """Daily usage broken down by top N users (for stacked charts)."""
    import datetime
    from collections import defaultdict

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    # Find top N users by total cost in period
    top_users_result = await db.execute(
        select(
            QueryUsage.user_id,
            User.username,
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("total_cost"),
        )
        .join(User, QueryUsage.user_id == User.id, isouter=True)
        .where(QueryUsage.created_at >= cutoff, QueryUsage.user_id.is_not(None))
        .group_by(QueryUsage.user_id, User.username)
        .order_by(sa_func.sum(QueryUsage.cost_usd).desc())
        .limit(top)
    )
    top_users = [(row.user_id, row.username or f"user_{row.user_id}") for row in top_users_result]
    if not top_users:
        return {"days": days, "users": [], "daily": []}

    top_user_ids = [uid for uid, _ in top_users]
    user_names = {uid: name for uid, name in top_users}

    # Get daily data for those users
    daily_result = await db.execute(
        select(
            sa_func.date_trunc("day", QueryUsage.created_at).label("day"),
            QueryUsage.user_id,
            sa_func.count(QueryUsage.id).label("count"),
            sa_func.coalesce(sa_func.sum(QueryUsage.cost_usd), 0).label("cost_usd"),
            sa_func.coalesce(sa_func.sum(QueryUsage.input_tokens), 0).label("input_tokens"),
            sa_func.coalesce(sa_func.sum(QueryUsage.output_tokens), 0).label("output_tokens"),
        )
        .where(QueryUsage.created_at >= cutoff, QueryUsage.user_id.in_(top_user_ids))
        .group_by("day", QueryUsage.user_id)
        .order_by("day")
    )

    # Build chart-friendly structure
    days_data: dict[str, dict[int, dict]] = defaultdict(dict)
    for row in daily_result:
        date_str = row.day.isoformat() if row.day else ""
        days_data[date_str][row.user_id] = {
            "count": row.count,
            "cost_usd": round(float(row.cost_usd), 6),
            "input_tokens": int(row.input_tokens),
            "output_tokens": int(row.output_tokens),
        }

    daily = []
    for date_str in sorted(days_data.keys()):
        entry: dict = {"date": date_str}
        for uid in top_user_ids:
            name = user_names[uid]
            entry[name] = days_data[date_str].get(uid, {
                "count": 0, "cost_usd": 0, "input_tokens": 0, "output_tokens": 0,
            })
        daily.append(entry)

    return {
        "days": days,
        "users": [user_names[uid] for uid in top_user_ids],
        "daily": daily,
    }
