"""
Webhooks API Router

Endpoints for receiving JIRA and GitHub webhooks.
"""

from fastapi import APIRouter, HTTPException, Request, Header, BackgroundTasks
from typing import Optional
import hmac
import hashlib
import json
from datetime import datetime

from db import get_db, generate_id
from config import settings
from services.jira_sync import process_jira_webhook
from services.github_handler import process_github_webhook
from websocket.manager import broadcast_message


router = APIRouter()


def verify_jira_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify JIRA webhook signature."""
    if not secret:
        return True  # Skip verification if no secret configured

    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature."""
    if not secret:
        return True  # Skip verification if no secret configured

    expected = "sha256=" + hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)


@router.post("/jira")
async def jira_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_atlassian_webhook_identifier: Optional[str] = Header(None),
):
    """Receive JIRA webhooks for ticket updates."""
    body = await request.body()

    # Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Extract event type
    event = payload.get("webhookEvent", "")

    # Process in background
    background_tasks.add_task(process_jira_webhook, payload)

    return {"status": "received", "event": event}


@router.post("/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(None),
    x_hub_signature_256: Optional[str] = Header(None),
):
    """Receive GitHub webhooks for PR updates."""
    body = await request.body()

    # Verify signature
    if settings.github_webhook_secret and x_hub_signature_256:
        if not verify_github_signature(body, x_hub_signature_256, settings.github_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Process relevant events
    relevant_events = [
        "pull_request_review_comment",
        "pull_request_review",
        "pull_request",
    ]

    if x_github_event not in relevant_events:
        return {"status": "ignored", "event": x_github_event}

    # Process in background
    background_tasks.add_task(process_github_webhook, x_github_event, payload)

    return {"status": "received", "event": x_github_event}
