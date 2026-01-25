"""
Session Store - Persistence helpers for Claude SDK sessions

Provides functions to save, restore, and manage PipelineSession state
in the database for pause/resume functionality.
"""

from datetime import datetime
from typing import Optional

from db import get_db, generate_id, json_dumps, json_loads


async def save_session(
    pipeline_id: str,
    session_id: str,
    cwd: str,
    ticket_context: dict,
    model: str = "claude-sonnet-4-20250514",
    last_step_completed: int = 0,
) -> str:
    """Save a new Claude session to the database.

    Args:
        pipeline_id: The pipeline this session belongs to
        session_id: The ClaudeSDKClient session ID
        cwd: Working directory for the session
        ticket_context: Ticket information (key, summary, description)
        model: Model being used
        last_step_completed: Last step that was completed

    Returns:
        The database record ID
    """
    db = await get_db()
    record_id = generate_id()
    now = datetime.utcnow().isoformat()

    await db.execute("""
        INSERT INTO claude_sessions
        (id, pipeline_id, claude_session_id, cwd, model, status,
         last_step_completed, ticket_context_json, created_at, last_active_at)
        VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
    """, (
        record_id,
        pipeline_id,
        session_id,
        cwd,
        model,
        last_step_completed,
        json_dumps(ticket_context),
        now,
        now,
    ))

    # Also update the pipeline with the session ID
    await db.execute("""
        UPDATE pipelines SET claude_session_id = ? WHERE id = ?
    """, (session_id, pipeline_id))

    await db.commit()
    return record_id


async def update_session_progress(
    pipeline_id: str,
    last_step_completed: int,
    conversation_summary: Optional[str] = None,
) -> None:
    """Update session progress after a step completes.

    Args:
        pipeline_id: The pipeline ID
        last_step_completed: The step that just completed
        conversation_summary: Optional summary for session reconstruction
    """
    db = await get_db()
    now = datetime.utcnow().isoformat()

    if conversation_summary:
        await db.execute("""
            UPDATE claude_sessions
            SET last_step_completed = ?, last_active_at = ?, conversation_summary = ?
            WHERE pipeline_id = ? AND status = 'active'
        """, (last_step_completed, now, conversation_summary, pipeline_id))
    else:
        await db.execute("""
            UPDATE claude_sessions
            SET last_step_completed = ?, last_active_at = ?
            WHERE pipeline_id = ? AND status = 'active'
        """, (last_step_completed, now, pipeline_id))

    await db.commit()


async def pause_session(
    pipeline_id: str,
    conversation_summary: Optional[str] = None,
) -> None:
    """Mark a session as paused.

    Args:
        pipeline_id: The pipeline ID
        conversation_summary: Summary of the session for later reconstruction
    """
    db = await get_db()
    now = datetime.utcnow().isoformat()

    await db.execute("""
        UPDATE claude_sessions
        SET status = 'paused', paused_at = ?, conversation_summary = COALESCE(?, conversation_summary)
        WHERE pipeline_id = ? AND status = 'active'
    """, (now, conversation_summary, pipeline_id))

    await db.commit()


async def get_session(pipeline_id: str) -> Optional[dict]:
    """Get the active or paused session for a pipeline.

    Args:
        pipeline_id: The pipeline ID

    Returns:
        Session record dict or None if not found
    """
    db = await get_db()

    session = await db.fetchone("""
        SELECT * FROM claude_sessions
        WHERE pipeline_id = ? AND status IN ('active', 'paused')
        ORDER BY created_at DESC LIMIT 1
    """, (pipeline_id,))

    if session and session.get('ticket_context_json'):
        session['ticket_context'] = json_loads(session['ticket_context_json'])

    return session


async def complete_session(pipeline_id: str) -> None:
    """Mark a session as completed.

    Args:
        pipeline_id: The pipeline ID
    """
    db = await get_db()
    now = datetime.utcnow().isoformat()

    await db.execute("""
        UPDATE claude_sessions
        SET status = 'completed', completed_at = ?
        WHERE pipeline_id = ? AND status IN ('active', 'paused')
    """, (now, pipeline_id))

    await db.commit()


async def expire_session(pipeline_id: str) -> None:
    """Mark a session as expired (e.g., after timeout).

    Args:
        pipeline_id: The pipeline ID
    """
    db = await get_db()

    await db.execute("""
        UPDATE claude_sessions
        SET status = 'expired'
        WHERE pipeline_id = ? AND status IN ('active', 'paused')
    """, (pipeline_id,))

    await db.commit()


async def save_session_state(
    pipeline_id: str,
    state_json: str,
) -> None:
    """Save session state JSON to the pipeline record.

    This is used for more detailed state preservation beyond just
    the conversation summary.

    Args:
        pipeline_id: The pipeline ID
        state_json: Serialized session state
    """
    db = await get_db()

    await db.execute("""
        UPDATE pipelines SET session_state_json = ? WHERE id = ?
    """, (state_json, pipeline_id))

    await db.commit()


async def get_session_state(pipeline_id: str) -> Optional[str]:
    """Get saved session state JSON from the pipeline record.

    Args:
        pipeline_id: The pipeline ID

    Returns:
        Session state JSON string or None
    """
    db = await get_db()

    pipeline = await db.fetchone(
        "SELECT session_state_json FROM pipelines WHERE id = ?",
        (pipeline_id,)
    )

    return pipeline.get('session_state_json') if pipeline else None


async def generate_conversation_summary(
    pipeline_id: str,
    max_steps: int = 8,
) -> str:
    """Generate a conversation summary from step outputs.

    This creates a summary of all completed steps that can be used
    to restore context when resuming a paused session.

    Args:
        pipeline_id: The pipeline ID
        max_steps: Maximum number of steps

    Returns:
        A summary string of completed work
    """
    db = await get_db()

    steps = await db.fetchall("""
        SELECT ps.step_number, ps.step_name, ps.status, so.content, so.content_json
        FROM pipeline_steps ps
        LEFT JOIN step_outputs so ON so.step_id = ps.id
        WHERE ps.pipeline_id = ?
        ORDER BY ps.step_number
    """, (pipeline_id,))

    summary_parts = []
    for step in steps:
        if step['status'] == 'completed' and step['content']:
            # Truncate content to a reasonable length for the summary
            content = step['content']
            if len(content) > 2000:
                content = content[:2000] + "...[truncated]"

            summary_parts.append(f"""
### Step {step['step_number']}: {step['step_name']}
{content}
""")

    if not summary_parts:
        return "No steps have been completed yet."

    return f"""## Pipeline Progress Summary

The following steps have been completed:
{"".join(summary_parts)}
"""


# ============================================================
# TOKEN BUFFER MANAGEMENT
# ============================================================

async def save_token_buffer(
    pipeline_id: str,
    step_number: int,
    tokens: str,
    ttl_seconds: int = 300,  # 5 minutes default
) -> str:
    """Save buffered tokens for reconnection catchup.

    This stores streaming tokens temporarily so that clients who disconnect
    and reconnect can catch up on missed tokens.

    Args:
        pipeline_id: The pipeline ID
        step_number: The current step number
        tokens: The token string to buffer
        ttl_seconds: Time to live in seconds (default 5 minutes)

    Returns:
        The buffer record ID
    """
    db = await get_db()
    buffer_id = generate_id()
    now = datetime.utcnow()
    expires_at = datetime.utcfromtimestamp(now.timestamp() + ttl_seconds).isoformat()

    await db.execute("""
        INSERT INTO token_buffer (id, pipeline_id, step_number, tokens, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        buffer_id,
        pipeline_id,
        step_number,
        tokens,
        now.isoformat(),
        expires_at,
    ))

    await db.commit()
    return buffer_id


async def get_buffered_tokens(
    pipeline_id: str,
    step_number: int,
    since: Optional[str] = None,
) -> list[dict]:
    """Get buffered tokens for a pipeline step.

    Args:
        pipeline_id: The pipeline ID
        step_number: The step number
        since: Optional ISO timestamp to get tokens after

    Returns:
        List of token buffer records
    """
    db = await get_db()
    now = datetime.utcnow().isoformat()

    if since:
        buffers = await db.fetchall("""
            SELECT * FROM token_buffer
            WHERE pipeline_id = ? AND step_number = ?
              AND created_at > ? AND expires_at > ?
            ORDER BY created_at ASC
        """, (pipeline_id, step_number, since, now))
    else:
        buffers = await db.fetchall("""
            SELECT * FROM token_buffer
            WHERE pipeline_id = ? AND step_number = ? AND expires_at > ?
            ORDER BY created_at ASC
        """, (pipeline_id, step_number, now))

    return [dict(b) for b in buffers]


async def append_to_token_buffer(
    pipeline_id: str,
    step_number: int,
    token: str,
    max_buffer_size: int = 50000,
    ttl_seconds: int = 300,
) -> None:
    """Append a token to the buffer for reconnection catchup.

    This maintains a rolling buffer of recent tokens. When the buffer
    gets too large, old entries are removed.

    Args:
        pipeline_id: The pipeline ID
        step_number: The current step number
        token: The token to append
        max_buffer_size: Maximum total characters to buffer
        ttl_seconds: Time to live for new entries
    """
    db = await get_db()
    now = datetime.utcnow()

    # Check current buffer size
    result = await db.fetchone("""
        SELECT COALESCE(SUM(LENGTH(tokens)), 0) as total_size
        FROM token_buffer
        WHERE pipeline_id = ? AND step_number = ? AND expires_at > ?
    """, (pipeline_id, step_number, now.isoformat()))

    current_size = result["total_size"] if result else 0

    # If buffer is getting large, remove oldest entries until we have room
    if current_size + len(token) > max_buffer_size:
        # Delete oldest entries to make room
        to_delete = (current_size + len(token)) - max_buffer_size + 1000  # Extra headroom
        oldest = await db.fetchall("""
            SELECT id, LENGTH(tokens) as size
            FROM token_buffer
            WHERE pipeline_id = ? AND step_number = ?
            ORDER BY created_at ASC
        """, (pipeline_id, step_number))

        deleted_size = 0
        for entry in oldest:
            if deleted_size >= to_delete:
                break
            await db.execute("DELETE FROM token_buffer WHERE id = ?", (entry["id"],))
            deleted_size += entry["size"]

    # Add new token
    await save_token_buffer(pipeline_id, step_number, token, ttl_seconds)


async def cleanup_expired_buffers() -> int:
    """Remove all expired token buffers.

    This should be called periodically (e.g., every minute) to clean up
    old buffer entries.

    Returns:
        Number of buffers removed
    """
    db = await get_db()
    now = datetime.utcnow().isoformat()

    # Get count before deletion for reporting
    result = await db.fetchone("""
        SELECT COUNT(*) as count FROM token_buffer WHERE expires_at <= ?
    """, (now,))
    count = result["count"] if result else 0

    # Delete expired entries
    await db.execute("DELETE FROM token_buffer WHERE expires_at <= ?", (now,))
    await db.commit()

    return count


async def clear_pipeline_buffer(pipeline_id: str, step_number: Optional[int] = None) -> None:
    """Clear the token buffer for a pipeline.

    This is called when a step completes or the pipeline ends.

    Args:
        pipeline_id: The pipeline ID
        step_number: Optional step number to clear (all if not specified)
    """
    db = await get_db()

    if step_number is not None:
        await db.execute("""
            DELETE FROM token_buffer WHERE pipeline_id = ? AND step_number = ?
        """, (pipeline_id, step_number))
    else:
        await db.execute("""
            DELETE FROM token_buffer WHERE pipeline_id = ?
        """, (pipeline_id,))

    await db.commit()


async def get_reconnection_data(
    pipeline_id: str,
    last_seen_step: int,
    last_seen_timestamp: str,
) -> dict:
    """Get all data needed for a client to catch up after reconnection.

    This retrieves:
    - Missed offline events
    - Buffered tokens since last seen
    - Current pipeline status

    Args:
        pipeline_id: The pipeline ID
        last_seen_step: The last step the client saw
        last_seen_timestamp: ISO timestamp of last received message

    Returns:
        Dict with catch-up data
    """
    db = await get_db()

    # Get current pipeline state
    pipeline = await db.fetchone("""
        SELECT status, current_step FROM pipelines WHERE id = ?
    """, (pipeline_id,))

    if not pipeline:
        return {"error": "Pipeline not found"}

    # Get any completed steps since the client was last connected
    completed_steps = await db.fetchall("""
        SELECT step_number, step_name, status, completed_at
        FROM pipeline_steps
        WHERE pipeline_id = ? AND completed_at > ? AND step_number >= ?
        ORDER BY step_number
    """, (pipeline_id, last_seen_timestamp, last_seen_step))

    # Get buffered tokens for current step (if still running)
    buffered_tokens = []
    if pipeline["status"] == "running":
        buffers = await get_buffered_tokens(
            pipeline_id,
            pipeline["current_step"],
            since=last_seen_timestamp
        )
        buffered_tokens = [b["tokens"] for b in buffers]

    return {
        "pipeline_status": pipeline["status"],
        "current_step": pipeline["current_step"],
        "completed_steps": [dict(s) for s in completed_steps],
        "buffered_tokens": buffered_tokens,
        "token_catchup": "".join(buffered_tokens),
    }
