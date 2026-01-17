#!/usr/bin/env python3
"""
Migration script to reconstruct chronological events from existing data.

For completed steps that have:
- tool_calls with timestamps
- text content (but no events array)

This script will:
1. Parse the text content to find segments between tool calls
2. Use tool call timestamps to estimate text segment times
3. Create proper events array with interleaved text and tool calls
4. Update the database
"""

import asyncio
import json
import re
from datetime import datetime, timedelta

import sys
sys.path.insert(0, '/home/eren/Projects/biagent/backend')

from db import get_db


# Patterns that indicate a new "thought" segment (agent deciding to do something)
THOUGHT_PATTERNS = [
    r'^Let me ',
    r'^Now let me ',
    r'^I\'ll ',
    r'^Now I\'ll ',
    r'^First,? let me ',
    r'^Next,? let me ',
    r'^I need to ',
    r'^I should ',
    r'^Looking at ',
    r'^Based on ',
    r'^The ',
    r'^This ',
    r'^---',  # Markdown separator
    r'^#',    # Markdown header
]


def split_text_into_segments(text: str) -> list[str]:
    """Split text into logical segments based on thought patterns."""
    if not text:
        return []

    # Split by common patterns that indicate new thoughts/actions
    # We look for these patterns at the start of lines
    lines = text.split('\n')
    segments = []
    current_segment = []

    for line in lines:
        # Check if this line starts a new segment
        is_new_segment = False
        for pattern in THOUGHT_PATTERNS:
            if re.match(pattern, line.strip(), re.IGNORECASE):
                is_new_segment = True
                break

        if is_new_segment and current_segment:
            # Save current segment and start new one
            segment_text = '\n'.join(current_segment).strip()
            if segment_text:
                segments.append(segment_text)
            current_segment = [line]
        else:
            current_segment.append(line)

    # Don't forget the last segment
    if current_segment:
        segment_text = '\n'.join(current_segment).strip()
        if segment_text:
            segments.append(segment_text)

    return segments


def interleave_events(text_segments: list[str], tool_calls: list[dict]) -> list[dict]:
    """
    Interleave text segments with tool calls chronologically.

    Strategy:
    - If we have N tool calls with timestamps, we have N+1 possible text positions
    - Position 0: before first tool call
    - Position i (1 to N): after tool call i-1
    - We distribute text segments across these positions based on count
    """
    if not tool_calls:
        # No tool calls, just return text as single event
        if text_segments:
            return [{"type": "text", "content": '\n\n'.join(text_segments)}]
        return []

    events = []
    num_tools = len(tool_calls)
    num_texts = len(text_segments)

    # Sort tool calls by timestamp
    sorted_tools = sorted(tool_calls, key=lambda x: x.get('timestamp', ''))

    if num_texts == 0:
        # Only tool calls
        for tc in sorted_tools:
            events.append({
                "type": "tool_call",
                "tool": tc["tool"],
                "arguments": tc.get("arguments", {}),
                "timestamp": tc.get("timestamp"),
            })
        return events

    # Distribute text segments around tool calls
    # Heuristic: first segment is usually "thinking before first tool"
    # Then alternate: tool, text, tool, text...
    # Any remaining text goes at the end

    text_idx = 0

    # First text segment (before any tool call)
    if text_idx < num_texts:
        first_text = text_segments[text_idx]
        # Check if it looks like intro text (short, starts with "Let me")
        if len(first_text) < 500 or first_text.lower().startswith('let me'):
            # Get timestamp slightly before first tool call
            first_tool_time = sorted_tools[0].get('timestamp')
            if first_tool_time:
                try:
                    dt = datetime.fromisoformat(first_tool_time.replace('Z', '+00:00'))
                    text_time = (dt - timedelta(seconds=2)).isoformat()
                except:
                    text_time = first_tool_time
            else:
                text_time = None

            events.append({
                "type": "text",
                "content": first_text,
                "timestamp": text_time,
            })
            text_idx += 1

    # Interleave tool calls with remaining text
    for i, tc in enumerate(sorted_tools):
        # Add tool call
        events.append({
            "type": "tool_call",
            "tool": tc["tool"],
            "arguments": tc.get("arguments", {}),
            "timestamp": tc.get("timestamp"),
        })

        # Add text after this tool call (if available and not the last tool)
        if text_idx < num_texts and i < num_tools - 1:
            # Timestamp between this tool and next
            this_time = tc.get('timestamp')
            next_time = sorted_tools[i + 1].get('timestamp') if i + 1 < num_tools else None

            if this_time and next_time:
                try:
                    dt1 = datetime.fromisoformat(this_time.replace('Z', '+00:00'))
                    dt2 = datetime.fromisoformat(next_time.replace('Z', '+00:00'))
                    text_time = (dt1 + (dt2 - dt1) / 2).isoformat()
                except:
                    text_time = this_time
            else:
                text_time = this_time

            events.append({
                "type": "text",
                "content": text_segments[text_idx],
                "timestamp": text_time,
            })
            text_idx += 1

    # Any remaining text goes at the end (final output)
    if text_idx < num_texts:
        remaining_text = '\n\n'.join(text_segments[text_idx:])
        last_tool_time = sorted_tools[-1].get('timestamp')
        if last_tool_time:
            try:
                dt = datetime.fromisoformat(last_tool_time.replace('Z', '+00:00'))
                text_time = (dt + timedelta(seconds=2)).isoformat()
            except:
                text_time = last_tool_time
        else:
            text_time = None

        events.append({
            "type": "text",
            "content": remaining_text,
            "timestamp": text_time,
        })

    return events


async def migrate_step(db, step_id: str, step_number: int, pipeline_id: str) -> bool:
    """Migrate a single step to have proper events."""

    # Get current output
    output = await db.fetchone("""
        SELECT id, content, content_json FROM step_outputs
        WHERE step_id = ?
        ORDER BY created_at DESC LIMIT 1
    """, (step_id,))

    if not output:
        return False

    # Check if already has events
    if output['content_json']:
        try:
            existing = json.loads(output['content_json'])
            if existing.get('events') and len(existing['events']) > 0:
                print(f"  Step {step_number}: Already has {len(existing['events'])} events, skipping")
                return False
        except:
            pass

    # Get tool calls with timestamps
    tool_calls = await db.fetchall("""
        SELECT tool_name, arguments, created_at
        FROM tool_calls
        WHERE step_id = ?
        ORDER BY created_at ASC
    """, (step_id,))

    tool_call_list = []
    for tc in tool_calls:
        args = {}
        if tc['arguments']:
            try:
                args = json.loads(tc['arguments'])
            except:
                args = {"raw": tc['arguments']}

        tool_call_list.append({
            "tool": tc['tool_name'],
            "arguments": args,
            "timestamp": tc['created_at'],
        })

    # Get text content
    text_content = output['content'] or ''

    if not text_content and not tool_call_list:
        print(f"  Step {step_number}: No content or tool calls, skipping")
        return False

    # Split text into segments
    text_segments = split_text_into_segments(text_content)

    # Interleave into events
    events = interleave_events(text_segments, tool_call_list)

    print(f"  Step {step_number}: {len(tool_call_list)} tools, {len(text_segments)} text segments -> {len(events)} events")

    # Update content_json
    content_json_data = {
        "events": events,
        "structured_output": None,
    }

    await db.execute("""
        UPDATE step_outputs
        SET content_json = ?
        WHERE id = ?
    """, (json.dumps(content_json_data), output['id']))

    return True


async def main():
    """Run the migration."""
    db = await get_db()

    # Get all completed pipelines
    pipelines = await db.fetchall("""
        SELECT id, ticket_key FROM pipelines
        WHERE status IN ('completed', 'paused', 'failed')
        ORDER BY created_at DESC
    """)

    print(f"Found {len(pipelines)} completed pipelines to migrate")

    total_migrated = 0

    for pipeline in pipelines:
        print(f"\nPipeline {pipeline['id']} ({pipeline['ticket_key']}):")

        # Get all steps
        steps = await db.fetchall("""
            SELECT id, step_number FROM pipeline_steps
            WHERE pipeline_id = ?
            ORDER BY step_number
        """, (pipeline['id'],))

        for step in steps:
            migrated = await migrate_step(
                db,
                step['id'],
                step['step_number'],
                pipeline['id']
            )
            if migrated:
                total_migrated += 1

    await db.commit()
    print(f"\n\nMigration complete! Migrated {total_migrated} steps.")


if __name__ == '__main__':
    asyncio.run(main())
