"""
GitHub Webhook Handler Service

Processes GitHub webhooks for PR reviews and comments.
"""

import asyncio
from datetime import datetime
from typing import Optional

from db import get_db, generate_id
from config import settings
from websocket.manager import broadcast_message


# Debounce state
_pending_reviews: dict[str, asyncio.Task] = {}
DEBOUNCE_SECONDS = 30


async def process_github_webhook(event_type: str, payload: dict):
    """Process incoming GitHub webhook."""
    action = payload.get("action", "")

    if event_type == "pull_request_review_comment":
        await handle_review_comment(payload)
    elif event_type == "pull_request_review":
        await handle_review(payload)
    elif event_type == "pull_request":
        await handle_pr_event(payload, action)


async def handle_review_comment(payload: dict):
    """Handle individual review comment."""
    db = await get_db()

    comment = payload.get("comment", {})
    pr = payload.get("pull_request", {})
    pr_number = pr.get("number")

    if not pr_number:
        return

    # Find subscription for this PR
    subscription = await db.fetchone("""
        SELECT * FROM webhook_subscriptions
        WHERE resource_type = 'pull_request' AND resource_id = ? AND active = TRUE
    """, (str(pr_number),))

    if not subscription:
        return

    # Find PR record
    pr_record = await db.fetchone("""
        SELECT * FROM pull_requests WHERE pr_number = ?
    """, (pr_number,))

    if not pr_record:
        return

    # Save comment
    comment_id = generate_id()
    now = datetime.utcnow().isoformat()

    await db.execute("""
        INSERT INTO review_comments
        (id, pr_id, github_comment_id, comment_body, file_path, line_number,
         reviewer, review_state, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'comment', ?)
    """, (
        comment_id,
        pr_record["id"],
        str(comment.get("id")),
        comment.get("body", ""),
        comment.get("path"),
        comment.get("line"),
        comment.get("user", {}).get("login"),
        now,
    ))
    await db.commit()

    # Debounce: wait for more comments before triggering agent
    await debounce_review_processing(pr_record["id"], subscription["pipeline_id"])

    # Broadcast
    await broadcast_message({
        "type": "review_received",
        "pipeline_id": subscription["pipeline_id"],
        "comments": [{
            "body": comment.get("body", "")[:100],
            "file": comment.get("path"),
            "line": comment.get("line"),
            "reviewer": comment.get("user", {}).get("login"),
        }],
    })


async def handle_review(payload: dict):
    """Handle review submission (approve/changes requested)."""
    db = await get_db()

    review = payload.get("review", {})
    pr = payload.get("pull_request", {})
    pr_number = pr.get("number")
    state = review.get("state", "").lower()

    if not pr_number:
        return

    # Find subscription
    subscription = await db.fetchone("""
        SELECT * FROM webhook_subscriptions
        WHERE resource_type = 'pull_request' AND resource_id = ? AND active = TRUE
    """, (str(pr_number),))

    if not subscription:
        return

    # Find PR record
    pr_record = await db.fetchone("""
        SELECT * FROM pull_requests WHERE pr_number = ?
    """, (pr_number,))

    if not pr_record:
        return

    now = datetime.utcnow().isoformat()

    if state == "approved":
        # Update PR as approved
        await db.execute("""
            UPDATE pull_requests
            SET approval_count = approval_count + 1, approved_at = ?, status = 'approved'
            WHERE id = ?
        """, (now, pr_record["id"]))

        # Update step 8 as completed
        await db.execute("""
            UPDATE pipeline_steps
            SET status = 'completed', completed_at = ?
            WHERE pipeline_id = ? AND step_number = 8
        """, (now, subscription["pipeline_id"]))

        # Update pipeline as completed
        await db.execute("""
            UPDATE pipelines
            SET status = 'completed', completed_at = ?
            WHERE id = ?
        """, (now, subscription["pipeline_id"]))

        await db.commit()

        # Broadcast
        await broadcast_message({
            "type": "pr_approved",
            "pipeline_id": subscription["pipeline_id"],
            "approvals": pr_record["approval_count"] + 1,
            "approved_by": review.get("user", {}).get("login"),
        })

        await broadcast_message({
            "type": "pipeline_completed",
            "pipeline_id": subscription["pipeline_id"],
        })

    elif state == "changes_requested":
        # Save review body as comment
        if review.get("body"):
            comment_id = generate_id()
            await db.execute("""
                INSERT INTO review_comments
                (id, pr_id, github_comment_id, comment_body, reviewer, review_state, created_at)
                VALUES (?, ?, ?, ?, ?, 'changes_requested', ?)
            """, (
                comment_id,
                pr_record["id"],
                str(review.get("id")),
                review.get("body", ""),
                review.get("user", {}).get("login"),
                now,
            ))
            await db.commit()

        # Broadcast
        await broadcast_message({
            "type": "changes_requested",
            "pipeline_id": subscription["pipeline_id"],
            "comment_count": 1,
            "reviewer": review.get("user", {}).get("login"),
        })

        # Trigger agent
        await debounce_review_processing(pr_record["id"], subscription["pipeline_id"])


async def handle_pr_event(payload: dict, action: str):
    """Handle PR state changes (merged, closed)."""
    db = await get_db()

    pr = payload.get("pull_request", {})
    pr_number = pr.get("number")

    if not pr_number:
        return

    pr_record = await db.fetchone("""
        SELECT * FROM pull_requests WHERE pr_number = ?
    """, (pr_number,))

    if not pr_record:
        return

    now = datetime.utcnow().isoformat()

    if action == "closed":
        if pr.get("merged"):
            await db.execute("""
                UPDATE pull_requests SET status = 'merged', merged_at = ? WHERE id = ?
            """, (now, pr_record["id"]))
        else:
            await db.execute("""
                UPDATE pull_requests SET status = 'closed' WHERE id = ?
            """, (pr_record["id"],))

        await db.commit()


async def debounce_review_processing(pr_id: str, pipeline_id: str):
    """Debounce review processing to batch comments."""
    # Cancel existing task
    if pr_id in _pending_reviews:
        _pending_reviews[pr_id].cancel()

    # Create new debounced task
    _pending_reviews[pr_id] = asyncio.create_task(
        _process_reviews_after_delay(pr_id, pipeline_id)
    )


async def _process_reviews_after_delay(pr_id: str, pipeline_id: str):
    """Process reviews after debounce delay."""
    await asyncio.sleep(DEBOUNCE_SECONDS)

    db = await get_db()

    # Get unprocessed comments
    comments = await db.fetchall("""
        SELECT * FROM review_comments
        WHERE pr_id = ? AND processed = FALSE
        ORDER BY created_at
    """, (pr_id,))

    if not comments:
        return

    # Trigger Review Agent (Step 8)
    from services.pipeline_engine import PipelineEngine

    engine = PipelineEngine(pipeline_id)
    asyncio.create_task(engine.run_review_step(comments))

    # Clean up
    if pr_id in _pending_reviews:
        del _pending_reviews[pr_id]
