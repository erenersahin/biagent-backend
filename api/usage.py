"""
Usage Tracking API

Track and aggregate usage metrics for both consumer and organization tiers.
Provides endpoints for recording usage events and retrieving usage statistics.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional
from datetime import datetime, timedelta
from calendar import monthrange
import logging

from middleware.auth import (
    get_current_user,
    get_current_org,
    get_current_user_optional,
    CurrentUser,
    CurrentOrg,
    CurrentUserOptional,
    clerk_auth,
)
from schemas.organization import (
    UsageEventCreate,
    UsageAggregateResponse,
)
from db import get_db
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


# Plan limits (pipelines per month)
PLAN_LIMITS = {
    "trial": 10,
    "consumer": float("inf"),  # Unlimited for consumer tier
    "starter": 50,
    "growth": 200,
    "enterprise": float("inf"),
}


async def get_org_plan(org_id: str) -> str:
    """Get the plan for an organization."""
    # TODO: Look up from database
    return "trial"


async def record_usage_event(
    event_type: str,
    resource_id: Optional[str] = None,
    org_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tokens_input: int = 0,
    tokens_output: int = 0,
    cost_usd: float = 0.0,
):
    """Record a usage event (internal function)."""
    db = await get_db()

    # Ensure usage_events table exists
    await db.execute("""
        CREATE TABLE IF NOT EXISTS usage_events (
            id TEXT PRIMARY KEY,
            org_id TEXT,
            user_id TEXT,
            event_type TEXT NOT NULL,
            resource_id TEXT,
            tokens_input INTEGER DEFAULT 0,
            tokens_output INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    await db.commit()

    from db import generate_id
    event_id = generate_id()

    await db.execute("""
        INSERT INTO usage_events (id, org_id, user_id, event_type, resource_id, tokens_input, tokens_output, cost_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (event_id, org_id, user_id, event_type, resource_id, tokens_input, tokens_output, cost_usd))
    await db.commit()

    logger.info(f"Recorded usage event: {event_type} for org={org_id}, user={user_id}")
    return event_id


async def get_monthly_usage(
    org_id: Optional[str] = None,
    user_id: Optional[str] = None,
    months: int = 6,
) -> List[dict]:
    """Get aggregated monthly usage."""
    db = await get_db()

    # Build query based on context
    if org_id:
        where_clause = "WHERE org_id = ?"
        params = [org_id]
    elif user_id:
        where_clause = "WHERE user_id = ? AND org_id IS NULL"
        params = [user_id]
    else:
        where_clause = ""
        params = []

    # Get the cutoff date
    cutoff = (datetime.utcnow() - timedelta(days=months * 31)).strftime("%Y-%m-01")
    if where_clause:
        where_clause += " AND created_at >= ?"
    else:
        where_clause = "WHERE created_at >= ?"
    params.append(cutoff)

    query = f"""
        SELECT
            strftime('%Y-%m', created_at) as month,
            COUNT(CASE WHEN event_type = 'pipeline_run' THEN 1 END) as pipelines_run,
            SUM(COALESCE(tokens_input, 0) + COALESCE(tokens_output, 0)) as total_tokens,
            SUM(COALESCE(cost_usd, 0)) as total_cost_usd
        FROM usage_events
        {where_clause}
        GROUP BY strftime('%Y-%m', created_at)
        ORDER BY month DESC
    """

    try:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        return [
            {
                "month": row[0],
                "pipelines_run": row[1] or 0,
                "total_tokens": row[2] or 0,
                "total_cost_usd": row[3] or 0.0,
            }
            for row in rows
        ]
    except Exception as e:
        logger.warning(f"Error fetching monthly usage: {e}")
        return []


async def get_current_month_stats(
    org_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict:
    """Get usage stats for the current billing month."""
    db = await get_db()

    # Current month boundaries
    now = datetime.utcnow()
    month_start = now.strftime("%Y-%m-01")
    _, last_day = monthrange(now.year, now.month)
    month_end = f"{now.year}-{now.month:02d}-{last_day}"

    # Build query
    if org_id:
        where_clause = "WHERE org_id = ? AND created_at >= ? AND created_at <= ?"
        params = [org_id, month_start, month_end]
    elif user_id:
        where_clause = "WHERE user_id = ? AND org_id IS NULL AND created_at >= ? AND created_at <= ?"
        params = [user_id, month_start, month_end]
    else:
        where_clause = "WHERE created_at >= ? AND created_at <= ?"
        params = [month_start, month_end]

    query = f"""
        SELECT
            COUNT(CASE WHEN event_type = 'pipeline_run' THEN 1 END) as pipelines_run,
            SUM(COALESCE(tokens_input, 0) + COALESCE(tokens_output, 0)) as total_tokens,
            SUM(COALESCE(cost_usd, 0)) as total_cost_usd
        FROM usage_events
        {where_clause}
    """

    try:
        cursor = await db.execute(query, params)
        row = await cursor.fetchone()

        pipelines_run = row[0] or 0 if row else 0
        total_tokens = row[1] or 0 if row else 0
        total_cost_usd = row[2] or 0.0 if row else 0.0

        # Get plan limit
        plan = await get_org_plan(org_id) if org_id else "consumer"
        plan_limit = PLAN_LIMITS.get(plan, 10)

        usage_percentage = (pipelines_run / plan_limit * 100) if plan_limit != float("inf") else 0.0

        return {
            "month": now.strftime("%Y-%m"),
            "pipelines_run": pipelines_run,
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost_usd,
            "plan": plan,
            "plan_limit": plan_limit if plan_limit != float("inf") else None,
            "usage_percentage": min(usage_percentage, 100.0),
            "is_unlimited": plan_limit == float("inf"),
        }
    except Exception as e:
        logger.warning(f"Error fetching current month stats: {e}")
        return {
            "month": now.strftime("%Y-%m"),
            "pipelines_run": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "plan": "trial",
            "plan_limit": 10,
            "usage_percentage": 0.0,
            "is_unlimited": False,
        }


# === API Endpoints ===


@router.get("/", response_model=List[UsageAggregateResponse])
async def get_usage(
    months: int = Query(default=6, ge=1, le=24, description="Number of months to retrieve"),
    user: CurrentUserOptional = None,
):
    """
    Get usage statistics for the current context.

    For authenticated users with an org, returns org usage.
    For authenticated users without org, returns personal usage.
    For unauthenticated (local mode), returns all local usage.
    """
    org_id = None
    user_id = None

    if user:
        # Check if user has current org context
        if user.organizations:
            org_id = user.organizations[0].id
        else:
            user_id = user.id

    usage = await get_monthly_usage(org_id=org_id, user_id=user_id, months=months)
    return usage


@router.get("/current-month")
async def get_current_month_usage(user: CurrentUserOptional = None):
    """
    Get usage for the current billing period.

    Includes plan limit information and usage percentage.
    """
    org_id = None
    user_id = None

    if user:
        if user.organizations:
            org_id = user.organizations[0].id
        else:
            user_id = user.id

    return await get_current_month_stats(org_id=org_id, user_id=user_id)


@router.post("/record", status_code=status.HTTP_201_CREATED)
async def record_usage(
    event: UsageEventCreate,
    user: CurrentUserOptional = None,
):
    """
    Record a usage event.

    This endpoint is typically called internally by the pipeline engine.
    """
    org_id = None
    user_id = None

    if user:
        if user.organizations:
            org_id = user.organizations[0].id
        user_id = user.id

    event_id = await record_usage_event(
        event_type=event.event_type,
        resource_id=event.resource_id,
        org_id=org_id,
        user_id=user_id,
        tokens_input=event.tokens_input or 0,
        tokens_output=event.tokens_output or 0,
        cost_usd=event.cost_usd or 0.0,
    )

    return {"id": event_id, "recorded": True}


@router.get("/limits")
async def get_usage_limits(user: CurrentUserOptional = None):
    """
    Get the usage limits for the current context.
    """
    if user and user.organizations:
        org_id = user.organizations[0].id
        plan = await get_org_plan(org_id)
    elif settings.tier == "consumer":
        plan = "consumer"
    else:
        plan = "trial"

    limit = PLAN_LIMITS.get(plan, 10)

    return {
        "plan": plan,
        "pipelines_per_month": limit if limit != float("inf") else None,
        "is_unlimited": limit == float("inf"),
        "tier": settings.tier,
    }


@router.get("/check")
async def check_usage_allowed(user: CurrentUserOptional = None):
    """
    Check if a new pipeline run is allowed based on usage limits.

    Returns whether the user/org can run a pipeline.
    """
    stats = await get_current_month_usage(user)

    if stats.get("is_unlimited"):
        return {"allowed": True, "reason": "Unlimited plan"}

    plan_limit = stats.get("plan_limit", 10)
    pipelines_run = stats.get("pipelines_run", 0)

    if pipelines_run >= plan_limit:
        return {
            "allowed": False,
            "reason": f"Monthly limit reached ({pipelines_run}/{plan_limit} pipelines)",
            "upgrade_required": True,
        }

    remaining = plan_limit - pipelines_run
    return {
        "allowed": True,
        "remaining": remaining,
        "usage": f"{pipelines_run}/{plan_limit}",
    }
