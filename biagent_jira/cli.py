"""
BiAgent JIRA CLI

Command-line interface for JIRA ticket operations.

Commands:
  biagent-jira list             List tickets from the local cache
  biagent-jira get PROJ-123     Get details of a specific ticket
  biagent-jira related PROJ-123 Show related/linked tickets
  biagent-jira update PROJ-123  Update a ticket's status
  biagent-jira sync             Manually trigger a JIRA sync
"""

import asyncio
import sys
from typing import Optional
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

app = typer.Typer(
    name="biagent-jira",
    help="BiAgent JIRA CLI - Interact with JIRA tickets",
    add_completion=False,
)

console = Console()


def get_db_connection():
    """Get an async database connection."""
    from db import get_db
    return get_db()


@app.command("list")
def list_tickets(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="Filter by assignee"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Filter by project key"),
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum number of tickets"),
    format: str = typer.Option("table", "--format", "-f", help="Output format: table, json, simple"),
):
    """List tickets from the local cache."""

    async def _list():
        db = await get_db_connection()

        # Build query
        query = "SELECT key, summary, status, priority, assignee, project_key FROM tickets WHERE 1=1"
        params = []

        if status:
            query += " AND LOWER(status) LIKE LOWER(?)"
            params.append(f"%{status}%")

        if assignee:
            query += " AND LOWER(assignee) LIKE LOWER(?)"
            params.append(f"%{assignee}%")

        if project:
            query += " AND UPPER(project_key) = UPPER(?)"
            params.append(project)

        query += " ORDER BY local_updated_at DESC LIMIT ?"
        params.append(limit)

        tickets = await db.fetchall(query, params)

        if format == "json":
            import json
            console.print_json(json.dumps([dict(t) for t in tickets]))
            return

        if not tickets:
            console.print("[yellow]No tickets found[/yellow]")
            return

        if format == "simple":
            for t in tickets:
                status_color = "green" if t["status"] == "Done" else "yellow" if "progress" in t["status"].lower() else "blue"
                console.print(f"[bold]{t['key']}[/bold] [{status_color}]{t['status']}[/{status_color}] {t['summary'][:60]}")
            return

        # Table format
        table = Table(title=f"JIRA Tickets ({len(tickets)} found)")
        table.add_column("Key", style="cyan", no_wrap=True)
        table.add_column("Status", style="green")
        table.add_column("Priority")
        table.add_column("Summary", max_width=50)
        table.add_column("Assignee")

        for t in tickets:
            priority_color = "red" if t["priority"] == "High" else "yellow" if t["priority"] == "Medium" else "dim"
            table.add_row(
                t["key"],
                t["status"],
                f"[{priority_color}]{t['priority'] or '-'}[/{priority_color}]",
                t["summary"][:50] + ("..." if len(t["summary"]) > 50 else ""),
                t["assignee"] or "-",
            )

        console.print(table)

    asyncio.run(_list())


@app.command("get")
def get_ticket(
    ticket_key: str = typer.Argument(..., help="Ticket key (e.g., PROJ-123)"),
    format: str = typer.Option("rich", "--format", "-f", help="Output format: rich, json, markdown"),
):
    """Get details of a specific ticket."""

    async def _get():
        db = await get_db_connection()

        ticket = await db.fetchone(
            "SELECT * FROM tickets WHERE UPPER(key) = UPPER(?)",
            (ticket_key,)
        )

        if not ticket:
            console.print(f"[red]Ticket {ticket_key} not found[/red]")
            raise typer.Exit(1)

        if format == "json":
            import json
            # Remove raw_json from output to keep it clean
            t = dict(ticket)
            t.pop("raw_json", None)
            console.print_json(json.dumps(t))
            return

        if format == "markdown":
            console.print(f"# {ticket['key']}: {ticket['summary']}")
            console.print(f"\n**Status:** {ticket['status']}")
            console.print(f"**Priority:** {ticket['priority'] or 'Not set'}")
            console.print(f"**Assignee:** {ticket['assignee'] or 'Unassigned'}")
            console.print(f"**Project:** {ticket['project_key']}")
            if ticket["epic_key"]:
                console.print(f"**Epic:** {ticket['epic_key']} - {ticket['epic_name']}")
            console.print(f"\n## Description\n\n{ticket['description'] or 'No description'}")
            return

        # Rich format
        status_color = "green" if ticket["status"] == "Done" else "yellow" if "progress" in ticket["status"].lower() else "blue"

        panel_content = f"""[bold cyan]{ticket['key']}[/bold cyan]: {ticket['summary']}

[bold]Status:[/bold] [{status_color}]{ticket['status']}[/{status_color}]
[bold]Priority:[/bold] {ticket['priority'] or 'Not set'}
[bold]Assignee:[/bold] {ticket['assignee'] or 'Unassigned'}
[bold]Project:[/bold] {ticket['project_key']}
[bold]Type:[/bold] {ticket['issue_type']}"""

        if ticket["epic_key"]:
            panel_content += f"\n[bold]Epic:[/bold] {ticket['epic_key']} - {ticket['epic_name']}"

        console.print(Panel(panel_content, title="Ticket Details"))

        if ticket["description"]:
            console.print("\n[bold]Description:[/bold]")
            console.print(Markdown(ticket["description"]))

        # Show attachments
        attachments = await db.fetchall(
            "SELECT filename, mime_type, size FROM ticket_attachments WHERE ticket_key = ?",
            (ticket_key,)
        )
        if attachments:
            console.print("\n[bold]Attachments:[/bold]")
            for att in attachments:
                size_kb = att["size"] / 1024 if att["size"] else 0
                console.print(f"  - {att['filename']} ({size_kb:.1f} KB)")

    asyncio.run(_get())


@app.command("related")
def related_tickets(
    ticket_key: str = typer.Argument(..., help="Ticket key (e.g., PROJ-123)"),
):
    """Show related/linked tickets."""

    async def _related():
        db = await get_db_connection()

        # Check ticket exists
        ticket = await db.fetchone(
            "SELECT key, summary FROM tickets WHERE UPPER(key) = UPPER(?)",
            (ticket_key,)
        )

        if not ticket:
            console.print(f"[red]Ticket {ticket_key} not found[/red]")
            raise typer.Exit(1)

        # Get links
        links = await db.fetchall("""
            SELECT tl.target_key, tl.link_type, t.summary, t.status
            FROM ticket_links tl
            LEFT JOIN tickets t ON t.key = tl.target_key
            WHERE tl.source_key = ?
        """, (ticket_key,))

        # Also get reverse links (tickets linking TO this one)
        reverse_links = await db.fetchall("""
            SELECT tl.source_key as target_key, tl.link_type, t.summary, t.status
            FROM ticket_links tl
            LEFT JOIN tickets t ON t.key = tl.source_key
            WHERE tl.target_key = ?
        """, (ticket_key,))

        # Get tickets in same epic
        epic_siblings = []
        if ticket.get("epic_key"):
            epic_siblings = await db.fetchall("""
                SELECT key, summary, status FROM tickets
                WHERE epic_key = ? AND key != ?
                LIMIT 10
            """, (ticket.get("epic_key"), ticket_key))

        console.print(Panel(f"[bold cyan]{ticket['key']}[/bold cyan]: {ticket['summary']}", title="Related Tickets"))

        if links:
            console.print("\n[bold]Outgoing Links:[/bold]")
            table = Table()
            table.add_column("Type", style="yellow")
            table.add_column("Ticket", style="cyan")
            table.add_column("Summary")
            table.add_column("Status", style="green")

            for link in links:
                table.add_row(
                    link["link_type"] or "relates to",
                    link["target_key"],
                    (link["summary"] or "")[:40],
                    link["status"] or "?",
                )
            console.print(table)

        if reverse_links:
            console.print("\n[bold]Incoming Links:[/bold]")
            table = Table()
            table.add_column("Type", style="yellow")
            table.add_column("Ticket", style="cyan")
            table.add_column("Summary")
            table.add_column("Status", style="green")

            for link in reverse_links:
                table.add_row(
                    f"is {link['link_type'] or 'related'} by",
                    link["target_key"],
                    (link["summary"] or "")[:40],
                    link["status"] or "?",
                )
            console.print(table)

        if epic_siblings:
            console.print("\n[bold]Same Epic:[/bold]")
            for sib in epic_siblings:
                console.print(f"  - [cyan]{sib['key']}[/cyan] [{sib['status']}] {sib['summary'][:50]}")

        if not links and not reverse_links and not epic_siblings:
            console.print("[yellow]No related tickets found[/yellow]")

    asyncio.run(_related())


@app.command("update")
def update_ticket(
    ticket_key: str = typer.Argument(..., help="Ticket key (e.g., PROJ-123)"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="New status"),
    comment: Optional[str] = typer.Option(None, "--comment", "-c", help="Add a comment"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be updated without making changes"),
):
    """Update a ticket's status or add a comment.

    Note: This updates the JIRA API directly (not just local cache).
    Requires JIRA API credentials to be configured.
    """
    import httpx
    from config import settings

    async def _update():
        if not all([settings.jira_base_url, settings.jira_email, settings.jira_api_token]):
            console.print("[red]JIRA credentials not configured[/red]")
            console.print("Set BIAGENT_JIRA_BASE_URL, BIAGENT_JIRA_EMAIL, and BIAGENT_JIRA_API_TOKEN")
            raise typer.Exit(1)

        db = await get_db_connection()

        # Verify ticket exists locally
        ticket = await db.fetchone(
            "SELECT * FROM tickets WHERE UPPER(key) = UPPER(?)",
            (ticket_key,)
        )

        if not ticket:
            console.print(f"[red]Ticket {ticket_key} not found in local cache[/red]")
            console.print("Try running 'biagent-jira sync' first")
            raise typer.Exit(1)

        if dry_run:
            console.print("[yellow]DRY RUN - No changes will be made[/yellow]\n")

        console.print(f"[bold]Updating {ticket_key}:[/bold] {ticket['summary'][:50]}")

        if status:
            if dry_run:
                console.print(f"  Would change status from [yellow]{ticket['status']}[/yellow] to [green]{status}[/green]")
            else:
                # Get available transitions
                async with httpx.AsyncClient() as client:
                    transitions_url = f"{settings.jira_base_url}/rest/api/3/issue/{ticket_key}/transitions"
                    response = await client.get(
                        transitions_url,
                        auth=(settings.jira_email, settings.jira_api_token),
                    )

                    if response.status_code != 200:
                        console.print(f"[red]Failed to get transitions: {response.text}[/red]")
                        raise typer.Exit(1)

                    transitions = response.json().get("transitions", [])
                    matching = [t for t in transitions if status.lower() in t["name"].lower()]

                    if not matching:
                        console.print(f"[red]No transition found for status '{status}'[/red]")
                        console.print("Available transitions:")
                        for t in transitions:
                            console.print(f"  - {t['name']}")
                        raise typer.Exit(1)

                    transition_id = matching[0]["id"]

                    # Perform transition
                    response = await client.post(
                        transitions_url,
                        json={"transition": {"id": transition_id}},
                        auth=(settings.jira_email, settings.jira_api_token),
                        headers={"Content-Type": "application/json"},
                    )

                    if response.status_code in (200, 204):
                        console.print(f"  [green]Status changed to {matching[0]['name']}[/green]")

                        # Update local cache
                        await db.execute(
                            "UPDATE tickets SET status = ? WHERE key = ?",
                            (matching[0]["name"], ticket_key)
                        )
                        await db.commit()
                    else:
                        console.print(f"[red]Failed to update status: {response.text}[/red]")
                        raise typer.Exit(1)

        if comment:
            if dry_run:
                console.print(f"  Would add comment: {comment[:50]}...")
            else:
                async with httpx.AsyncClient() as client:
                    comment_url = f"{settings.jira_base_url}/rest/api/3/issue/{ticket_key}/comment"
                    response = await client.post(
                        comment_url,
                        json={
                            "body": {
                                "type": "doc",
                                "version": 1,
                                "content": [{
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": comment}]
                                }]
                            }
                        },
                        auth=(settings.jira_email, settings.jira_api_token),
                        headers={"Content-Type": "application/json"},
                    )

                    if response.status_code == 201:
                        console.print(f"  [green]Comment added[/green]")
                    else:
                        console.print(f"[red]Failed to add comment: {response.text}[/red]")
                        raise typer.Exit(1)

        if not status and not comment:
            console.print("[yellow]No updates specified. Use --status or --comment[/yellow]")

    asyncio.run(_update())


@app.command("sync")
def sync_tickets(
    force: bool = typer.Option(False, "--force", "-f", help="Force full sync even if recently synced"),
):
    """Manually trigger a JIRA sync."""
    from services.jira_sync import sync_tickets as do_sync

    async def _sync():
        from config import settings

        if not all([settings.jira_base_url, settings.jira_email, settings.jira_api_token]):
            console.print("[red]JIRA credentials not configured[/red]")
            console.print("Set BIAGENT_JIRA_BASE_URL, BIAGENT_JIRA_EMAIL, and BIAGENT_JIRA_API_TOKEN")
            raise typer.Exit(1)

        console.print("[yellow]Syncing tickets from JIRA...[/yellow]")

        try:
            count = await do_sync(sync_type="manual")
            console.print(f"[green]Synced {count} tickets[/green]")
        except Exception as e:
            console.print(f"[red]Sync failed: {e}[/red]")
            raise typer.Exit(1)

    asyncio.run(_sync())


@app.command("search")
def search_tickets(
    query: str = typer.Argument(..., help="Search query (searches summary and description)"),
    limit: int = typer.Option(10, "--limit", "-l", help="Maximum results"),
):
    """Search tickets by keyword."""

    async def _search():
        db = await get_db_connection()

        tickets = await db.fetchall("""
            SELECT key, summary, status, description
            FROM tickets
            WHERE LOWER(summary) LIKE LOWER(?) OR LOWER(description) LIKE LOWER(?)
            ORDER BY local_updated_at DESC
            LIMIT ?
        """, (f"%{query}%", f"%{query}%", limit))

        if not tickets:
            console.print(f"[yellow]No tickets found matching '{query}'[/yellow]")
            return

        console.print(f"[bold]Found {len(tickets)} tickets matching '{query}':[/bold]\n")

        for t in tickets:
            status_color = "green" if t["status"] == "Done" else "yellow" if "progress" in t["status"].lower() else "blue"
            console.print(f"[bold cyan]{t['key']}[/bold cyan] [{status_color}]{t['status']}[/{status_color}]")
            console.print(f"  {t['summary']}")

            # Show snippet of description where query matches
            if t["description"] and query.lower() in t["description"].lower():
                idx = t["description"].lower().find(query.lower())
                start = max(0, idx - 30)
                end = min(len(t["description"]), idx + len(query) + 30)
                snippet = t["description"][start:end]
                if start > 0:
                    snippet = "..." + snippet
                if end < len(t["description"]):
                    snippet = snippet + "..."
                console.print(f"  [dim]...{snippet}...[/dim]")
            console.print()

    asyncio.run(_search())


if __name__ == "__main__":
    app()
