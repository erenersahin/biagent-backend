"""
JIRA Sync Service

Handles syncing tickets from JIRA to local SQLite cache.
"""

import asyncio
import httpx
from datetime import datetime
from typing import Optional

from db import get_db, generate_id, json_dumps
from config import settings
from websocket.manager import broadcast_message


# Background task reference
_sync_task: Optional[asyncio.Task] = None


def adf_to_text(adf: dict) -> str:
    """Convert Atlassian Document Format (ADF) to plain text."""
    if not adf or not isinstance(adf, dict):
        return ""

    def extract_text(node: dict) -> str:
        """Recursively extract text from ADF node."""
        if not isinstance(node, dict):
            return ""

        node_type = node.get("type", "")
        text_parts = []

        # Direct text node
        if node_type == "text":
            return node.get("text", "")

        # Handle content array
        content = node.get("content", [])
        for child in content:
            text_parts.append(extract_text(child))

        # Join based on node type
        if node_type in ("paragraph", "heading"):
            return "".join(text_parts) + "\n"
        elif node_type == "bulletList":
            return "\n".join(f"• {part.strip()}" for part in text_parts if part.strip()) + "\n"
        elif node_type == "orderedList":
            return "\n".join(f"{i+1}. {part.strip()}" for i, part in enumerate(text_parts) if part.strip()) + "\n"
        elif node_type == "listItem":
            return "".join(text_parts)
        elif node_type == "codeBlock":
            return "```\n" + "".join(text_parts) + "```\n"
        elif node_type == "blockquote":
            return "> " + "".join(text_parts)
        else:
            return "".join(text_parts)

    result = extract_text(adf)
    # Clean up multiple newlines
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result.strip()


async def fetch_jira_tickets(jql: Optional[str] = None) -> list[dict]:
    """Fetch tickets from JIRA API."""
    if not all([settings.jira_base_url, settings.jira_email, settings.jira_api_token]):
        return []

    if jql is None:
        if settings.jira_project_key:
            jql = f"project = {settings.jira_project_key} ORDER BY updated DESC"
        else:
            jql = "assignee = currentUser() ORDER BY updated DESC"

    url = f"{settings.jira_base_url}/rest/api/3/search/jql"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json={
                "jql": jql,
                "maxResults": 100,
                "fields": ["summary", "description", "status", "priority", "assignee", "project", "issuetype", "created", "updated", "parent"],
            },
            auth=(settings.jira_email, settings.jira_api_token),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()

    return data.get("issues", [])


def parse_jira_ticket(issue: dict) -> dict:
    """Parse JIRA API response into ticket dict."""
    fields = issue.get("fields", {})

    # Parse description - handle ADF format or plain text
    description_raw = fields.get("description")
    if isinstance(description_raw, dict):
        description = adf_to_text(description_raw)
    elif description_raw:
        description = str(description_raw)
    else:
        description = ""

    # Parse parent/epic info
    parent = fields.get("parent", {})
    epic_key = parent.get("key") if parent else None
    epic_name = parent.get("fields", {}).get("summary") if parent else None

    return {
        "id": issue["id"],
        "key": issue["key"],
        "summary": fields.get("summary", ""),
        "description": description,
        "status": fields.get("status", {}).get("name", "Unknown"),
        "priority": fields.get("priority", {}).get("name") if fields.get("priority") else None,
        "assignee": fields.get("assignee", {}).get("displayName") if fields.get("assignee") else None,
        "project_key": fields.get("project", {}).get("key"),
        "issue_type": fields.get("issuetype", {}).get("name", "feature"),
        "epic_key": epic_key,
        "epic_name": epic_name,
        "created_at": fields.get("created"),
        "updated_at": fields.get("updated"),
        "jira_updated_at": fields.get("updated"),
        "raw_json": json_dumps(issue),
    }


async def sync_tickets(sync_type: str = "auto") -> int:
    """Sync tickets from JIRA to local database."""
    db = await get_db()

    try:
        # Fetch tickets
        issues = await fetch_jira_tickets()

        # Update database
        count = 0
        for issue in issues:
            ticket = parse_jira_ticket(issue)
            now = datetime.utcnow().isoformat()

            # Upsert ticket
            await db.execute("""
                INSERT INTO tickets
                (id, key, summary, description, status, priority, assignee,
                 project_key, issue_type, epic_key, epic_name, created_at, updated_at,
                 jira_updated_at, local_updated_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    summary = excluded.summary,
                    description = excluded.description,
                    status = excluded.status,
                    priority = excluded.priority,
                    assignee = excluded.assignee,
                    epic_key = excluded.epic_key,
                    epic_name = excluded.epic_name,
                    updated_at = excluded.updated_at,
                    jira_updated_at = excluded.jira_updated_at,
                    local_updated_at = ?,
                    raw_json = excluded.raw_json
            """, (
                ticket["id"], ticket["key"], ticket["summary"], ticket["description"],
                ticket["status"], ticket["priority"], ticket["assignee"],
                ticket["project_key"], ticket["issue_type"], ticket["epic_key"],
                ticket["epic_name"], ticket["created_at"], ticket["updated_at"],
                ticket["jira_updated_at"], now, ticket["raw_json"], now
            ))
            count += 1

        # Record sync
        await db.execute("""
            INSERT INTO sync_status (last_sync_at, sync_type, tickets_updated)
            VALUES (?, ?, ?)
        """, (datetime.utcnow().isoformat(), sync_type, count))

        await db.commit()

        # Broadcast update
        await broadcast_message({
            "type": "sync_complete",
            "count": count,
            "timestamp": datetime.utcnow().isoformat(),
        })

        return count

    except Exception as e:
        # Record error
        await db.execute("""
            INSERT INTO sync_status (last_sync_at, sync_type, tickets_updated, error)
            VALUES (?, ?, 0, ?)
        """, (datetime.utcnow().isoformat(), sync_type, str(e)))
        await db.commit()

        await broadcast_message({
            "type": "sync_error",
            "message": str(e),
        })

        raise


async def process_jira_webhook(payload: dict):
    """Process incoming JIRA webhook."""
    db = await get_db()

    event = payload.get("webhookEvent", "")
    issue = payload.get("issue", {})

    if not issue:
        return

    ticket_key = issue.get("key")
    if not ticket_key:
        return

    now = datetime.utcnow().isoformat()

    if event == "jira:issue_deleted":
        # Delete ticket
        await db.execute("DELETE FROM tickets WHERE key = ?", (ticket_key,))
    else:
        # Update ticket
        ticket = parse_jira_ticket(issue)
        await db.execute("""
            INSERT INTO tickets
            (id, key, summary, description, status, priority, assignee,
             project_key, issue_type, created_at, updated_at, jira_updated_at,
             local_updated_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                summary = excluded.summary,
                description = excluded.description,
                status = excluded.status,
                priority = excluded.priority,
                assignee = excluded.assignee,
                updated_at = excluded.updated_at,
                jira_updated_at = excluded.jira_updated_at,
                local_updated_at = ?,
                raw_json = excluded.raw_json
        """, (
            ticket["id"], ticket["key"], ticket["summary"], ticket["description"],
            ticket["status"], ticket["priority"], ticket["assignee"],
            ticket["project_key"], ticket["issue_type"], ticket["created_at"],
            ticket["updated_at"], ticket["jira_updated_at"], now,
            ticket["raw_json"], now
        ))

    # Record sync
    await db.execute("""
        INSERT INTO sync_status (last_sync_at, sync_type, tickets_updated)
        VALUES (?, 'webhook', 1)
    """, (now,))

    await db.commit()

    # Broadcast update
    await broadcast_message({
        "type": "ticket_updated",
        "id": issue.get("id"),
        "key": ticket_key,
        "changes": ["status", "description"],  # Simplified
    })


async def sync_scheduler():
    """Background task that runs periodic JIRA sync."""
    while True:
        try:
            await sync_tickets(sync_type="auto")
        except Exception as e:
            print(f"Sync error: {e}")

        await asyncio.sleep(settings.jira_sync_interval_minutes * 60)


async def start_sync_scheduler():
    """Start the background sync scheduler."""
    global _sync_task

    # Start scheduler (initial sync will happen on first iteration)
    _sync_task = asyncio.create_task(sync_scheduler())


async def stop_sync_scheduler():
    """Stop the background sync scheduler."""
    global _sync_task

    if _sync_task:
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
        _sync_task = None
