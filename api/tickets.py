"""
Tickets API Router

Endpoints for managing JIRA ticket cache.
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import Optional
from pydantic import BaseModel
from datetime import datetime
import httpx

from db import get_db, generate_id, json_dumps, json_loads
from config import settings


router = APIRouter()


class AttachmentResponse(BaseModel):
    """Attachment response model."""
    id: str
    filename: str
    mime_type: Optional[str] = None
    size: Optional[int] = None
    content_url: str
    thumbnail_url: Optional[str] = None
    author: Optional[str] = None
    created_at: Optional[str] = None


class TicketResponse(BaseModel):
    """Ticket response model."""
    id: str
    key: str
    summary: str
    description: Optional[str] = None
    status: str
    priority: Optional[str] = None
    assignee: Optional[str] = None
    project_key: Optional[str] = None
    issue_type: Optional[str] = None
    epic_key: Optional[str] = None
    epic_name: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    pipeline_status: Optional[str] = None
    attachments: list[AttachmentResponse] = []


class TicketListResponse(BaseModel):
    """Response for ticket list."""
    tickets: list[TicketResponse]
    total: int
    last_synced: Optional[str] = None


class TicketStatsResponse(BaseModel):
    """Response for ticket stats."""
    total: int
    completed: int
    in_progress: int
    pending: int
    failed: int


class AppConfigResponse(BaseModel):
    """App configuration for frontend."""
    developer_name: Optional[str] = None
    jira_project_key: Optional[str] = None
    max_steps: int = 8  # Number of pipeline steps enabled (1-8)


@router.get("/config", response_model=AppConfigResponse)
async def get_app_config():
    """Get app configuration for frontend."""
    return AppConfigResponse(
        developer_name=settings.developer_name,
        jira_project_key=settings.jira_project_key,
        max_steps=settings.max_steps,
    )


@router.get("", response_model=TicketListResponse)
async def list_tickets(
    status: Optional[str] = None,
    assignee: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
):
    """List all cached JIRA tickets."""
    db = await get_db()

    # Build query
    conditions = []
    params = []

    if status:
        conditions.append("t.status = ?")
        params.append(status)

    if assignee:
        conditions.append("t.assignee = ?")
        params.append(assignee)

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    # Get tickets with pipeline status
    query = f"""
        SELECT
            t.*,
            p.status as pipeline_status
        FROM tickets t
        LEFT JOIN (
            SELECT ticket_key, status
            FROM pipelines
            WHERE id IN (
                SELECT MAX(id) FROM pipelines GROUP BY ticket_key
            )
        ) p ON t.key = p.ticket_key
        {where_clause}
        ORDER BY t.updated_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    tickets = await db.fetchall(query, tuple(params))

    # Get total count
    count_query = f"SELECT COUNT(*) as count FROM tickets t {where_clause}"
    count_params = params[:-2] if params else []
    count_result = await db.fetchone(count_query, tuple(count_params))
    total = count_result["count"] if count_result else 0

    # Get last sync time
    sync = await db.fetchone(
        "SELECT last_sync_at FROM sync_status ORDER BY id DESC LIMIT 1"
    )
    last_synced = sync["last_sync_at"] if sync else None

    return TicketListResponse(
        tickets=[TicketResponse(**t) for t in tickets],
        total=total,
        last_synced=last_synced,
    )


@router.get("/stats", response_model=TicketStatsResponse)
async def get_ticket_stats(assignee: Optional[str] = None):
    """Get ticket statistics, optionally filtered by assignee."""
    db = await get_db()

    # Build where clause
    where_clause = ""
    params = []
    if assignee:
        where_clause = "WHERE t.assignee = ?"
        params = [assignee]

    # Get total tickets
    total_result = await db.fetchone(
        f"SELECT COUNT(*) as count FROM tickets t {where_clause}",
        tuple(params)
    )
    total = total_result["count"] if total_result else 0

    # Get pipeline stats
    stats = await db.fetchall(f"""
        SELECT
            COALESCE(p.status, 'pending') as status,
            COUNT(*) as count
        FROM tickets t
        LEFT JOIN (
            SELECT ticket_key, status
            FROM pipelines
            WHERE id IN (
                SELECT MAX(id) FROM pipelines GROUP BY ticket_key
            )
        ) p ON t.key = p.ticket_key
        {where_clause}
        GROUP BY COALESCE(p.status, 'pending')
    """, tuple(params))

    stats_dict = {s["status"]: s["count"] for s in stats}

    return TicketStatsResponse(
        total=total,
        completed=stats_dict.get("completed", 0),
        in_progress=stats_dict.get("running", 0) + stats_dict.get("paused", 0),
        pending=stats_dict.get("pending", 0),
        failed=stats_dict.get("failed", 0),
    )


@router.get("/{ticket_key}", response_model=TicketResponse)
async def get_ticket(ticket_key: str):
    """Get a specific ticket by key."""
    db = await get_db()

    ticket = await db.fetchone("""
        SELECT
            t.*,
            p.status as pipeline_status
        FROM tickets t
        LEFT JOIN (
            SELECT ticket_key, status
            FROM pipelines
            WHERE id IN (
                SELECT MAX(id) FROM pipelines GROUP BY ticket_key
            )
        ) p ON t.key = p.ticket_key
        WHERE t.key = ?
    """, (ticket_key,))

    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_key} not found")

    # Fetch attachments for this ticket
    attachments = await db.fetchall("""
        SELECT id, filename, mime_type, size, content_url, thumbnail_url, author, created_at
        FROM ticket_attachments
        WHERE ticket_key = ?
        ORDER BY created_at DESC
    """, (ticket_key,))

    return TicketResponse(
        **ticket,
        attachments=[AttachmentResponse(**att) for att in attachments]
    )


@router.get("/{ticket_key}/related")
async def get_related_tickets(ticket_key: str):
    """Get tickets related to the given ticket."""
    db = await get_db()

    links = await db.fetchall("""
        SELECT
            tl.link_type,
            t.*
        FROM ticket_links tl
        JOIN tickets t ON tl.target_key = t.key
        WHERE tl.source_key = ?
    """, (ticket_key,))

    return {"related": links}


@router.post("/sync")
async def trigger_sync():
    """Manually trigger JIRA sync."""
    from services.jira_sync import sync_tickets

    try:
        count = await sync_tickets(sync_type="manual")
        return {"status": "success", "tickets_updated": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/attachments/{attachment_id}/content")
async def get_attachment_content(attachment_id: str):
    """Proxy endpoint to fetch attachment content from JIRA with authentication."""
    db = await get_db()

    # Get attachment from database
    attachment = await db.fetchone("""
        SELECT content_url, mime_type, filename
        FROM ticket_attachments
        WHERE id = ?
    """, (attachment_id,))

    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    content_url = attachment["content_url"]
    mime_type = attachment["mime_type"] or "application/octet-stream"
    filename = attachment["filename"]

    # Check if JIRA credentials are configured
    if not all([settings.jira_base_url, settings.jira_email, settings.jira_api_token]):
        raise HTTPException(status_code=500, detail="JIRA credentials not configured")

    try:
        # Fetch content from JIRA with authentication
        async with httpx.AsyncClient() as client:
            response = await client.get(
                content_url,
                auth=(settings.jira_email, settings.jira_api_token),
                follow_redirects=True,
                timeout=30.0,
            )
            response.raise_for_status()

            # Stream the response content
            async def generate():
                yield response.content

            return StreamingResponse(
                generate(),
                media_type=mime_type,
                headers={
                    "Content-Disposition": f'inline; filename="{filename}"',
                    "Cache-Control": "public, max-age=86400",  # Cache for 24 hours
                }
            )

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Failed to fetch attachment from JIRA")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to JIRA: {str(e)}")


@router.get("/attachments/{attachment_id}/thumbnail")
async def get_attachment_thumbnail(attachment_id: str):
    """Proxy endpoint to fetch attachment thumbnail from JIRA with authentication."""
    db = await get_db()

    # Get attachment from database
    attachment = await db.fetchone("""
        SELECT thumbnail_url, content_url, mime_type, filename
        FROM ticket_attachments
        WHERE id = ?
    """, (attachment_id,))

    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    # Use thumbnail URL if available, otherwise fall back to content URL
    url = attachment["thumbnail_url"] or attachment["content_url"]
    mime_type = attachment["mime_type"] or "image/png"
    filename = attachment["filename"]

    # Check if JIRA credentials are configured
    if not all([settings.jira_base_url, settings.jira_email, settings.jira_api_token]):
        raise HTTPException(status_code=500, detail="JIRA credentials not configured")

    try:
        # Fetch content from JIRA with authentication
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                auth=(settings.jira_email, settings.jira_api_token),
                follow_redirects=True,
                timeout=30.0,
            )
            response.raise_for_status()

            # Stream the response content
            async def generate():
                yield response.content

            return StreamingResponse(
                generate(),
                media_type=mime_type,
                headers={
                    "Content-Disposition": f'inline; filename="{filename}"',
                    "Cache-Control": "public, max-age=86400",  # Cache for 24 hours
                }
            )

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Failed to fetch thumbnail from JIRA")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to JIRA: {str(e)}")
