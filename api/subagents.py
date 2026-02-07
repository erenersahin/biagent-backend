"""
Subagents API Router

Endpoints for retrieving subagent tool call data for real-time activity display.
Subagent tool calls are captured when Claude uses the Task tool to spawn subagents
like Explore, Plan, etc. These tool calls are streamed in real-time via WebSocket
and persisted for page refresh.
"""

from fastapi import APIRouter
from typing import List
from pydantic import BaseModel
from datetime import datetime

from db import get_db, json_loads


router = APIRouter()


class SubagentToolCallResponse(BaseModel):
    """Response model for a subagent tool call."""
    id: str
    step_number: int
    parent_tool_use_id: str
    tool_use_id: str
    tool_name: str
    arguments: dict
    created_at: str


@router.get("/pipeline/{pipeline_id}", response_model=List[SubagentToolCallResponse])
async def get_pipeline_subagent_tool_calls(pipeline_id: str):
    """Get all subagent tool calls for a pipeline.

    Returns tool calls grouped by step and parent_tool_use_id,
    ordered by step_number and creation time.

    This is used to restore subagent activity display after page refresh.
    """
    db = await get_db()

    rows = await db.fetchall("""
        SELECT * FROM subagent_tool_calls
        WHERE pipeline_id = ?
        ORDER BY step_number, created_at
    """, (pipeline_id,))

    return [
        SubagentToolCallResponse(
            id=row["id"],
            step_number=row["step_number"],
            parent_tool_use_id=row["parent_tool_use_id"],
            tool_use_id=row["tool_use_id"],
            tool_name=row["tool_name"],
            arguments=json_loads(row["arguments"]) if row["arguments"] else {},
            created_at=row["created_at"],
        )
        for row in rows
    ]


@router.get("/step/{step_id}", response_model=List[SubagentToolCallResponse])
async def get_step_subagent_tool_calls(step_id: str):
    """Get all subagent tool calls for a specific step.

    Returns tool calls ordered by creation time.
    """
    db = await get_db()

    rows = await db.fetchall("""
        SELECT * FROM subagent_tool_calls
        WHERE step_id = ?
        ORDER BY created_at
    """, (step_id,))

    return [
        SubagentToolCallResponse(
            id=row["id"],
            step_number=row["step_number"],
            parent_tool_use_id=row["parent_tool_use_id"],
            tool_use_id=row["tool_use_id"],
            tool_name=row["tool_name"],
            arguments=json_loads(row["arguments"]) if row["arguments"] else {},
            created_at=row["created_at"],
        )
        for row in rows
    ]


@router.get("/parent/{parent_tool_use_id}", response_model=List[SubagentToolCallResponse])
async def get_parent_subagent_tool_calls(parent_tool_use_id: str):
    """Get all subagent tool calls for a specific Task tool invocation.

    This retrieves all tool calls made by a subagent spawned via a Task tool call,
    identified by the parent's tool_use_id.
    """
    db = await get_db()

    rows = await db.fetchall("""
        SELECT * FROM subagent_tool_calls
        WHERE parent_tool_use_id = ?
        ORDER BY created_at
    """, (parent_tool_use_id,))

    return [
        SubagentToolCallResponse(
            id=row["id"],
            step_number=row["step_number"],
            parent_tool_use_id=row["parent_tool_use_id"],
            tool_use_id=row["tool_use_id"],
            tool_name=row["tool_name"],
            arguments=json_loads(row["arguments"]) if row["arguments"] else {},
            created_at=row["created_at"],
        )
        for row in rows
    ]
