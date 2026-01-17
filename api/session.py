"""
Session API Router

Endpoints for session persistence and restoration.
"""

from fastapi import APIRouter, HTTPException
from typing import Optional
from pydantic import BaseModel
from datetime import datetime

from db import get_db, generate_id, json_dumps, json_loads


router = APIRouter()


class TabCreate(BaseModel):
    """Request to open a new tab."""
    ticket_key: str


class UIStateUpdate(BaseModel):
    """Request to update UI state."""
    active_tab: Optional[str] = None
    scroll_positions: Optional[dict] = None
    expanded_panels: Optional[list] = None


class SessionResponse(BaseModel):
    """Session restore response."""
    session_id: str
    tabs: list
    active_tab: Optional[str] = None
    missed_events: list


@router.get("/restore", response_model=SessionResponse)
async def restore_session(session_id: Optional[str] = None):
    """Restore session state on reconnect."""
    db = await get_db()

    # Get or create session
    if session_id:
        session = await db.fetchone(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
    else:
        session = None

    if not session:
        # Create new session
        session_id = generate_id()
        now = datetime.utcnow().isoformat()
        await db.execute("""
            INSERT INTO sessions (id, created_at, last_active_at)
            VALUES (?, ?, ?)
        """, (session_id, now, now))
        await db.commit()

        return SessionResponse(
            session_id=session_id,
            tabs=[],
            active_tab=None,
            missed_events=[],
        )

    # Load tabs
    tabs = await db.fetchall("""
        SELECT
            st.*,
            t.summary as ticket_summary,
            t.status as ticket_status,
            p.status as pipeline_status,
            p.current_step
        FROM session_tabs st
        JOIN tickets t ON st.ticket_key = t.key
        LEFT JOIN pipelines p ON st.pipeline_id = p.id
        WHERE st.session_id = ?
        ORDER BY st.tab_order
    """, (session_id,))

    # Load missed events
    events = await db.fetchall("""
        SELECT * FROM offline_events
        WHERE session_id = ? AND acknowledged = FALSE
        ORDER BY created_at ASC
    """, (session_id,))

    # Update last active
    now = datetime.utcnow().isoformat()
    await db.execute(
        "UPDATE sessions SET last_active_at = ? WHERE id = ?",
        (now, session_id)
    )
    await db.commit()

    return SessionResponse(
        session_id=session_id,
        tabs=tabs,
        active_tab=session["active_tab"],
        missed_events=events,
    )


@router.get("/tabs")
async def list_tabs(session_id: str):
    """List open tabs for a session."""
    db = await get_db()

    tabs = await db.fetchall("""
        SELECT
            st.*,
            t.summary as ticket_summary,
            p.status as pipeline_status,
            p.current_step
        FROM session_tabs st
        JOIN tickets t ON st.ticket_key = t.key
        LEFT JOIN pipelines p ON st.pipeline_id = p.id
        WHERE st.session_id = ?
        ORDER BY st.tab_order
    """, (session_id,))

    return {"tabs": tabs}


@router.post("/tabs")
async def open_tab(session_id: str, request: TabCreate):
    """Open a new tab."""
    db = await get_db()

    # Check if ticket exists
    ticket = await db.fetchone(
        "SELECT * FROM tickets WHERE key = ?", (request.ticket_key,)
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # Check if tab already exists
    existing = await db.fetchone("""
        SELECT * FROM session_tabs
        WHERE session_id = ? AND ticket_key = ?
    """, (session_id, request.ticket_key))

    if existing:
        return {"tab": existing, "already_open": True}

    # Get max tab order
    max_order = await db.fetchone("""
        SELECT COALESCE(MAX(tab_order), 0) as max_order
        FROM session_tabs WHERE session_id = ?
    """, (session_id,))

    # Get latest pipeline for this ticket
    pipeline = await db.fetchone("""
        SELECT id FROM pipelines
        WHERE ticket_key = ?
        ORDER BY created_at DESC LIMIT 1
    """, (request.ticket_key,))

    # Create tab
    tab_id = generate_id()
    now = datetime.utcnow().isoformat()

    await db.execute("""
        INSERT INTO session_tabs
        (id, session_id, ticket_key, pipeline_id, tab_order, opened_at, last_viewed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        tab_id,
        session_id,
        request.ticket_key,
        pipeline["id"] if pipeline else None,
        max_order["max_order"] + 1,
        now,
        now
    ))

    # Update active tab
    await db.execute(
        "UPDATE sessions SET active_tab = ? WHERE id = ?",
        (request.ticket_key, session_id)
    )

    await db.commit()

    return {
        "tab": {
            "id": tab_id,
            "ticket_key": request.ticket_key,
            "pipeline_id": pipeline["id"] if pipeline else None,
        },
        "already_open": False
    }


@router.delete("/tabs/{tab_id}")
async def close_tab(session_id: str, tab_id: str):
    """Close a tab."""
    db = await get_db()

    tab = await db.fetchone(
        "SELECT * FROM session_tabs WHERE id = ? AND session_id = ?",
        (tab_id, session_id)
    )
    if not tab:
        raise HTTPException(status_code=404, detail="Tab not found")

    await db.execute("DELETE FROM session_tabs WHERE id = ?", (tab_id,))

    # Update active tab if needed
    session = await db.fetchone(
        "SELECT active_tab FROM sessions WHERE id = ?", (session_id,)
    )
    if session and session["active_tab"] == tab["ticket_key"]:
        # Set next tab as active
        next_tab = await db.fetchone("""
            SELECT ticket_key FROM session_tabs
            WHERE session_id = ?
            ORDER BY tab_order LIMIT 1
        """, (session_id,))

        await db.execute(
            "UPDATE sessions SET active_tab = ? WHERE id = ?",
            (next_tab["ticket_key"] if next_tab else None, session_id)
        )

    await db.commit()

    return {"status": "closed"}


@router.put("/ui-state")
async def update_ui_state(session_id: str, request: UIStateUpdate):
    """Update UI state (debounced on client)."""
    db = await get_db()

    updates = []
    params = []

    if request.active_tab is not None:
        updates.append("active_tab = ?")
        params.append(request.active_tab)

    if request.scroll_positions is not None or request.expanded_panels is not None:
        ui_state = json_dumps({
            "scroll_positions": request.scroll_positions,
            "expanded_panels": request.expanded_panels,
        })
        updates.append("ui_state = ?")
        params.append(ui_state)

    if updates:
        updates.append("last_active_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(session_id)

        await db.execute(f"""
            UPDATE sessions SET {', '.join(updates)} WHERE id = ?
        """, tuple(params))
        await db.commit()

    return {"status": "updated"}


@router.post("/acknowledge-events")
async def acknowledge_events(session_id: str, event_ids: list[str]):
    """Mark offline events as acknowledged."""
    db = await get_db()

    if event_ids:
        placeholders = ",".join("?" * len(event_ids))
        await db.execute(f"""
            UPDATE offline_events
            SET acknowledged = TRUE
            WHERE session_id = ? AND id IN ({placeholders})
        """, (session_id, *event_ids))
        await db.commit()

    return {"status": "acknowledged", "count": len(event_ids)}
