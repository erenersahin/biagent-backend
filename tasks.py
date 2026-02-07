"""
BiAgent Pipeline Tasks

Entrypoint for executing the 8-step AI pipeline for JIRA ticket resolution.
This module defines the pipeline workflow that can be executed by a task queue.

Pipeline Steps:
    1. Context & Requirements - Gather ticket details and codebase context
    2. Risk & Blocker Analysis - Identify risks and dependencies
    3. Implementation Planning - Create detailed implementation plan
    4. Code Implementation - Write code on sandbox branch
    5. Test Writing & Execution - Write and run tests
    6. Documentation Updates - Update relevant documentation
    7. PR Creation - Create pull request with description
    8. Code Review Response - Handle PR feedback

Usage:
    # Start a new pipeline for a ticket
    await start_pipeline(ticket_key="PROJ-123")

    # Resume a paused pipeline
    await resume_pipeline(pipeline_id="...")
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Any
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# Pipeline Step Definitions
# =============================================================================

class StepType(Enum):
    """Pipeline step types mapping to agent implementations."""
    CONTEXT = "context"
    RISK = "risk"
    PLANNING = "planning"
    CODING = "coding"
    TESTING = "testing"
    DOCS = "docs"
    PR = "pr"
    REVIEW = "review"


@dataclass
class PipelineStep:
    """Definition of a pipeline step."""
    number: int
    name: str
    step_type: StepType
    description: str


# The 8-step pipeline definition
PIPELINE_STEPS = [
    PipelineStep(
        number=1,
        name="Context & Requirements",
        step_type=StepType.CONTEXT,
        description="Gather ticket details, analyze codebase, understand requirements",
    ),
    PipelineStep(
        number=2,
        name="Risk & Blocker Analysis",
        step_type=StepType.RISK,
        description="Identify risks, blockers, dependencies, and potential issues",
    ),
    PipelineStep(
        number=3,
        name="Implementation Planning",
        step_type=StepType.PLANNING,
        description="Create detailed implementation plan with file changes",
    ),
    PipelineStep(
        number=4,
        name="Code Implementation",
        step_type=StepType.CODING,
        description="Implement code changes on isolated sandbox branch",
    ),
    PipelineStep(
        number=5,
        name="Test Writing & Execution",
        step_type=StepType.TESTING,
        description="Write unit/integration tests and verify they pass",
    ),
    PipelineStep(
        number=6,
        name="Documentation Updates",
        step_type=StepType.DOCS,
        description="Update README, API docs, and inline comments",
    ),
    PipelineStep(
        number=7,
        name="PR Creation",
        step_type=StepType.PR,
        description="Create pull request with proper description",
    ),
    PipelineStep(
        number=8,
        name="Code Review Response",
        step_type=StepType.REVIEW,
        description="Address reviewer feedback and update PR",
    ),
]


# =============================================================================
# Pipeline Context
# =============================================================================

@dataclass
class PipelineContext:
    """Context passed through the pipeline."""
    pipeline_id: str
    ticket_key: str
    ticket_summary: str
    ticket_description: str
    repository_path: str
    worktree_paths: dict[str, str]  # repo_name -> worktree_path

    # Accumulated outputs from previous steps
    context_output: Optional[dict] = None    # Step 1
    risk_output: Optional[dict] = None       # Step 2
    plan_output: Optional[dict] = None       # Step 3
    code_output: Optional[dict] = None       # Step 4
    test_output: Optional[dict] = None       # Step 5
    docs_output: Optional[dict] = None       # Step 6
    pr_output: Optional[dict] = None         # Step 7


# =============================================================================
# Pipeline Task Interface
# =============================================================================

async def start_pipeline(
    ticket_key: str,
    repository_path: Optional[str] = None,  # noqa: ARG001 - reserved for future use
    on_step_started: Optional[Callable[[int, str], Any]] = None,  # noqa: ARG001
    on_step_completed: Optional[Callable[[int, str, dict], Any]] = None,  # noqa: ARG001
    on_token: Optional[Callable[[str], Any]] = None,  # noqa: ARG001
) -> str:
    """
    Start a new pipeline for a JIRA ticket.

    This is the main entrypoint for executing the BiAgent pipeline.
    Can be called directly or wrapped by a task queue (Celery, ARQ, etc.).

    Args:
        ticket_key: JIRA ticket key (e.g., "PROJ-123")
        repository_path: Optional path to target repository
        on_step_started: Callback when a step begins
        on_step_completed: Callback when a step completes
        on_token: Callback for streaming tokens

    Returns:
        pipeline_id: The created pipeline's unique identifier

    Example:
        # Direct usage
        pipeline_id = await start_pipeline("PROJ-123")

        # With callbacks
        pipeline_id = await start_pipeline(
            "PROJ-123",
            on_step_started=lambda step, name: print(f"Starting {name}"),
            on_step_completed=lambda step, name, output: print(f"Done {name}"),
        )
    """
    from db import get_db, generate_id
    from services.pipeline_engine import PipelineEngine

    db = await get_db()

    # Get ticket from database
    ticket = await db.fetchone(
        "SELECT * FROM tickets WHERE key = ?",
        (ticket_key,)
    )
    if not ticket:
        raise ValueError(f"Ticket {ticket_key} not found")

    # Create pipeline record
    pipeline_id = generate_id()
    await db.execute("""
        INSERT INTO pipelines (id, ticket_key, status, current_step, created_at)
        VALUES (?, ?, 'pending', 1, datetime('now'))
    """, (pipeline_id, ticket_key))

    # Create step records
    for step in PIPELINE_STEPS:
        await db.execute("""
            INSERT INTO pipeline_steps (id, pipeline_id, step_number, step_name, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (generate_id(), pipeline_id, step.number, step.name))

    await db.commit()

    # Update status to running
    await db.execute(
        "UPDATE pipelines SET status = 'running' WHERE id = ?",
        (pipeline_id,)
    )
    await db.commit()

    # Execute pipeline
    engine = PipelineEngine(pipeline_id)
    await engine.run()

    return pipeline_id


async def resume_pipeline(
    pipeline_id: str,
    from_step: Optional[int] = None,
) -> None:
    """
    Resume a paused or failed pipeline.

    Args:
        pipeline_id: The pipeline to resume
        from_step: Optional step number to restart from
    """
    from db import get_db
    from services.pipeline_engine import PipelineEngine

    db = await get_db()

    # Validate pipeline exists and can be resumed
    pipeline = await db.fetchone(
        "SELECT * FROM pipelines WHERE id = ?",
        (pipeline_id,)
    )
    if not pipeline:
        raise ValueError(f"Pipeline {pipeline_id} not found")

    if pipeline["status"] not in ("paused", "failed", "needs_user_input"):
        raise ValueError(f"Pipeline cannot be resumed from status: {pipeline['status']}")

    # Update step if specified
    if from_step:
        await db.execute(
            "UPDATE pipelines SET current_step = ? WHERE id = ?",
            (from_step, pipeline_id)
        )

    # Update status to running
    await db.execute(
        "UPDATE pipelines SET status = 'running', pause_requested = 0 WHERE id = ?",
        (pipeline_id,)
    )
    await db.commit()

    # Execute pipeline
    engine = PipelineEngine(pipeline_id)
    await engine.run()


async def pause_pipeline(pipeline_id: str) -> None:
    """
    Request a pipeline to pause after the current step completes.

    Args:
        pipeline_id: The pipeline to pause
    """
    from db import get_db

    db = await get_db()
    await db.execute(
        "UPDATE pipelines SET pause_requested = 1 WHERE id = ?",
        (pipeline_id,)
    )
    await db.commit()


async def cancel_pipeline(pipeline_id: str) -> None:
    """
    Cancel a running pipeline.

    Args:
        pipeline_id: The pipeline to cancel
    """
    from db import get_db

    db = await get_db()
    await db.execute(
        "UPDATE pipelines SET status = 'cancelled' WHERE id = ?",
        (pipeline_id,)
    )
    await db.commit()


# =============================================================================
# Pipeline Status
# =============================================================================

async def get_pipeline_status(pipeline_id: str) -> dict:
    """
    Get the current status of a pipeline.

    Returns:
        dict with status, current_step, step_statuses, etc.
    """
    from db import get_db

    db = await get_db()

    pipeline = await db.fetchone(
        "SELECT * FROM pipelines WHERE id = ?",
        (pipeline_id,)
    )
    if not pipeline:
        raise ValueError(f"Pipeline {pipeline_id} not found")

    steps = await db.fetchall("""
        SELECT step_number, step_name, status, tokens_used, cost
        FROM pipeline_steps
        WHERE pipeline_id = ?
        ORDER BY step_number
    """, (pipeline_id,))

    return {
        "pipeline_id": pipeline_id,
        "ticket_key": pipeline["ticket_key"],
        "status": pipeline["status"],
        "current_step": pipeline["current_step"],
        "steps": [dict(s) for s in steps],
        "total_tokens": sum(s["tokens_used"] or 0 for s in steps),
        "total_cost": sum(s["cost"] or 0 for s in steps),
    }


# =============================================================================
# Task Queue Integration Points
# =============================================================================

# These functions can be wrapped by task queue decorators:
#
# Celery:
#   @celery_app.task
#   def celery_start_pipeline(ticket_key: str):
#       asyncio.run(start_pipeline(ticket_key))
#
# ARQ:
#   async def arq_start_pipeline(ctx, ticket_key: str):
#       await start_pipeline(ticket_key)
#
# Dramatiq:
#   @dramatiq.actor
#   def dramatiq_start_pipeline(ticket_key: str):
#       asyncio.run(start_pipeline(ticket_key))
#
# Temporal:
#   @workflow.defn
#   class PipelineWorkflow:
#       @workflow.run
#       async def run(self, ticket_key: str):
#           await start_pipeline(ticket_key)
