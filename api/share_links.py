"""
Share Links API Router

Endpoints for creating and accessing read-only pipeline share links.
Allows sharing pipeline progress with stakeholders without authentication.
"""

import secrets
from fastapi import APIRouter, HTTPException
from typing import Optional
from pydantic import BaseModel
from datetime import datetime, timedelta

from db import get_db, generate_id


router = APIRouter()


class CreateShareLinkRequest(BaseModel):
    """Request to create a share link."""
    pipeline_id: str
    expires_in_hours: Optional[int] = None  # None = no expiration


class ShareLinkResponse(BaseModel):
    """Share link response model."""
    id: str
    pipeline_id: str
    token: str
    share_url: str
    created_at: str
    expires_at: Optional[str] = None
    view_count: int


class SharedPipelineResponse(BaseModel):
    """Response when accessing a shared pipeline."""
    pipeline: dict
    steps: list
    ticket: dict


@router.post("", response_model=ShareLinkResponse)
async def create_share_link(request: CreateShareLinkRequest):
    """Create a new share link for a pipeline."""
    db = await get_db()

    # Verify pipeline exists
    pipeline = await db.fetchone(
        "SELECT * FROM pipelines WHERE id = ?",
        (request.pipeline_id,)
    )
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Generate unique token
    token = secrets.token_urlsafe(16)
    share_id = generate_id()
    now = datetime.utcnow().isoformat()

    # Calculate expiration
    expires_at = None
    if request.expires_in_hours:
        expires_at = (datetime.utcnow() + timedelta(hours=request.expires_in_hours)).isoformat()

    # Create share link
    await db.execute("""
        INSERT INTO share_links (id, pipeline_id, token, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
    """, (share_id, request.pipeline_id, token, now, expires_at))
    await db.commit()

    return ShareLinkResponse(
        id=share_id,
        pipeline_id=request.pipeline_id,
        token=token,
        share_url=f"/share/{token}",
        created_at=now,
        expires_at=expires_at,
        view_count=0
    )


@router.get("/{token}")
async def get_shared_pipeline(token: str):
    """Access a shared pipeline by token.

    Returns pipeline data, steps, and ticket info for read-only viewing.
    """
    db = await get_db()

    # Find share link
    share_link = await db.fetchone(
        "SELECT * FROM share_links WHERE token = ?",
        (token,)
    )
    if not share_link:
        raise HTTPException(status_code=404, detail="Share link not found")

    # Check expiration
    if share_link["expires_at"]:
        expires_at = datetime.fromisoformat(share_link["expires_at"])
        if datetime.utcnow() > expires_at:
            raise HTTPException(status_code=410, detail="Share link has expired")

    # Update view count
    now = datetime.utcnow().isoformat()
    await db.execute("""
        UPDATE share_links
        SET view_count = view_count + 1, last_viewed_at = ?
        WHERE id = ?
    """, (now, share_link["id"]))
    await db.commit()

    # Get pipeline
    pipeline = await db.fetchone(
        "SELECT * FROM pipelines WHERE id = ?",
        (share_link["pipeline_id"],)
    )
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Get steps
    steps = await db.fetchall(
        "SELECT * FROM pipeline_steps WHERE pipeline_id = ? ORDER BY step_number",
        (pipeline["id"],)
    )

    # Get ticket
    ticket = await db.fetchone(
        "SELECT id, key, summary, description, status, priority, assignee, project_key, epic_name FROM tickets WHERE key = ?",
        (pipeline["ticket_key"],)
    )

    # Get step outputs for completed steps
    step_outputs = {}
    for step in steps:
        if step["status"] in ("completed", "failed", "skipped"):
            output = await db.fetchone(
                "SELECT content FROM step_outputs WHERE step_id = ?",
                (step["id"],)
            )
            if output and output["content"]:
                step_outputs[step["step_number"]] = output["content"]

    return {
        "pipeline": {
            "id": pipeline["id"],
            "ticket_key": pipeline["ticket_key"],
            "status": pipeline["status"],
            "current_step": pipeline["current_step"],
            "created_at": pipeline["created_at"],
            "started_at": pipeline["started_at"],
            "completed_at": pipeline["completed_at"],
            "total_tokens": pipeline["total_tokens"],
            "total_cost": pipeline["total_cost"],
        },
        "steps": [
            {
                "id": s["id"],
                "step_number": s["step_number"],
                "step_name": s["step_name"],
                "status": s["status"],
                "started_at": s["started_at"],
                "completed_at": s["completed_at"],
                "tokens_used": s["tokens_used"],
                "cost": s["cost"],
                "output": step_outputs.get(s["step_number"]),
            }
            for s in steps
        ],
        "ticket": dict(ticket) if ticket else None,
        "share_info": {
            "created_at": share_link["created_at"],
            "expires_at": share_link["expires_at"],
            "view_count": share_link["view_count"] + 1,
        }
    }


@router.get("/pipeline/{pipeline_id}/links")
async def get_pipeline_share_links(pipeline_id: str):
    """Get all share links for a pipeline."""
    db = await get_db()

    links = await db.fetchall("""
        SELECT * FROM share_links
        WHERE pipeline_id = ?
        ORDER BY created_at DESC
    """, (pipeline_id,))

    return {
        "links": [
            ShareLinkResponse(
                id=link["id"],
                pipeline_id=link["pipeline_id"],
                token=link["token"],
                share_url=f"/share/{link['token']}",
                created_at=link["created_at"],
                expires_at=link["expires_at"],
                view_count=link["view_count"]
            )
            for link in links
        ]
    }


@router.delete("/{share_id}")
async def delete_share_link(share_id: str):
    """Delete a share link."""
    db = await get_db()

    result = await db.execute(
        "DELETE FROM share_links WHERE id = ?",
        (share_id,)
    )
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Share link not found")

    return {"status": "deleted", "id": share_id}
