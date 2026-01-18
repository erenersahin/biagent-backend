"""
Pipelines API Router

Endpoints for managing pipeline execution.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Optional, Dict, List
from pydantic import BaseModel
from datetime import datetime

from db import get_db, generate_id, json_loads
from config import settings, get_step_config, get_step_name, STEP_CONFIGS
from services.pipeline_engine import PipelineEngine
from services.worktree_manager import WorktreeManager


router = APIRouter()


class PipelineCreate(BaseModel):
    """Request to create a new pipeline."""
    ticket_key: str


class PipelineResponse(BaseModel):
    """Pipeline response model."""
    id: str
    ticket_key: str
    status: str
    current_step: int
    created_at: str
    started_at: Optional[str] = None
    paused_at: Optional[str] = None
    completed_at: Optional[str] = None
    total_tokens: int
    total_cost: float
    # Worktree session data (included when status is needs_user_input)
    worktree_status: Optional[str] = None
    user_input_request: Optional[Dict] = None


class StepResponse(BaseModel):
    """Step response model."""
    id: str
    step_number: int
    step_name: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    tokens_used: int
    cost: float
    error_message: Optional[str] = None
    retry_count: int


class StepOutputResponse(BaseModel):
    """Step output response model."""
    id: str
    output_type: str
    content: Optional[str] = None
    content_json: Optional[dict] = None
    created_at: str


class FeedbackRequest(BaseModel):
    """Request to provide feedback on a step."""
    feedback: str


class RestartRequest(BaseModel):
    """Request to restart pipeline from a step."""
    from_step: int
    guidance: Optional[str] = None


class ProvideInputRequest(BaseModel):
    """Request to provide user input for pipeline (e.g., setup commands)."""
    input_type: str  # "setup_commands"
    data: Dict[str, List[str]]  # For setup_commands: repo_name -> list of commands


@router.post("", response_model=PipelineResponse)
async def create_pipeline(request: PipelineCreate):
    """Create a new pipeline for a ticket."""
    db = await get_db()

    # Check if ticket exists
    ticket = await db.fetchone(
        "SELECT * FROM tickets WHERE key = ?", (request.ticket_key,)
    )
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket {request.ticket_key} not found")

    # Create pipeline
    pipeline_id = generate_id()
    now = datetime.utcnow().isoformat()

    await db.execute("""
        INSERT INTO pipelines (id, ticket_key, status, current_step, created_at)
        VALUES (?, ?, 'pending', 1, ?)
    """, (pipeline_id, request.ticket_key, now))

    # Create steps up to max_steps (configurable, default 6 to skip PR/Review)
    max_steps = settings.max_steps
    for step_num, config in STEP_CONFIGS.items():
        if step_num > max_steps:
            continue
        step_id = generate_id()
        await db.execute("""
            INSERT INTO pipeline_steps (id, pipeline_id, step_number, step_name, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (step_id, pipeline_id, step_num, config["name"]))

    await db.commit()

    return PipelineResponse(
        id=pipeline_id,
        ticket_key=request.ticket_key,
        status="pending",
        current_step=1,
        created_at=now,
        total_tokens=0,
        total_cost=0.0,
    )


@router.get("/by-ticket/{ticket_key}", response_model=PipelineResponse)
async def get_pipeline_by_ticket(ticket_key: str):
    """Get the latest pipeline for a ticket."""
    db = await get_db()

    pipeline = await db.fetchone("""
        SELECT * FROM pipelines
        WHERE ticket_key = ?
        ORDER BY created_at DESC
        LIMIT 1
    """, (ticket_key,))

    if not pipeline:
        raise HTTPException(status_code=404, detail=f"No pipeline found for ticket {ticket_key}")

    response_data = dict(pipeline)

    # Include worktree session data if pipeline needs user input
    if pipeline["status"] == "needs_user_input":
        worktree_session = await db.fetchone(
            "SELECT status, user_input_request FROM worktree_sessions WHERE pipeline_id = ?",
            (pipeline["id"],)
        )
        if worktree_session:
            response_data["worktree_status"] = worktree_session["status"]
            if worktree_session["user_input_request"]:
                response_data["user_input_request"] = json_loads(worktree_session["user_input_request"])

    return PipelineResponse(**response_data)


@router.get("/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(pipeline_id: str):
    """Get pipeline status."""
    db = await get_db()

    pipeline = await db.fetchone(
        "SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)
    )
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    response_data = dict(pipeline)

    # Include worktree session data if pipeline needs user input
    if pipeline["status"] == "needs_user_input":
        worktree_session = await db.fetchone(
            "SELECT status, user_input_request FROM worktree_sessions WHERE pipeline_id = ?",
            (pipeline_id,)
        )
        if worktree_session:
            response_data["worktree_status"] = worktree_session["status"]
            if worktree_session["user_input_request"]:
                response_data["user_input_request"] = json_loads(worktree_session["user_input_request"])

    return PipelineResponse(**response_data)


@router.get("/{pipeline_id}/steps")
async def get_pipeline_steps(pipeline_id: str):
    """Get all steps for a pipeline."""
    db = await get_db()

    steps = await db.fetchall("""
        SELECT * FROM pipeline_steps
        WHERE pipeline_id = ?
        ORDER BY step_number
    """, (pipeline_id,))

    return {"steps": [StepResponse(**s) for s in steps]}


@router.get("/{pipeline_id}/steps/{step_number}")
async def get_step(pipeline_id: str, step_number: int):
    """Get a specific step."""
    db = await get_db()

    step = await db.fetchone("""
        SELECT * FROM pipeline_steps
        WHERE pipeline_id = ? AND step_number = ?
    """, (pipeline_id, step_number))

    if not step:
        raise HTTPException(status_code=404, detail="Step not found")

    return StepResponse(**step)


@router.get("/{pipeline_id}/outputs")
async def get_all_step_outputs(pipeline_id: str):
    """Get outputs and tool calls for ALL steps in a single request."""
    import json

    db = await get_db()

    # Get all steps for this pipeline
    steps = await db.fetchall("""
        SELECT id, step_number FROM pipeline_steps
        WHERE pipeline_id = ?
        ORDER BY step_number
    """, (pipeline_id,))

    if not steps:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    result = {}
    for step in steps:
        step_num = step["step_number"]
        step_id = step["id"]

        # Get latest output including content_json for events
        output = await db.fetchone("""
            SELECT content, content_json FROM step_outputs
            WHERE step_id = ?
            ORDER BY created_at DESC LIMIT 1
        """, (step_id,))

        # Parse content_json to get events
        events = []
        if output and output["content_json"]:
            try:
                content_json = json.loads(output["content_json"])
                events = content_json.get("events", [])
            except (json.JSONDecodeError, TypeError):
                pass

        # Fallback: get tool calls from tool_calls table if no events
        tool_calls = []
        if not events:
            tc_rows = await db.fetchall("""
                SELECT tool_name, arguments, created_at
                FROM tool_calls
                WHERE step_id = ?
                ORDER BY created_at ASC
            """, (step_id,))
            tool_calls = [
                {"tool": tc["tool_name"], "arguments": tc["arguments"], "timestamp": tc["created_at"]}
                for tc in tc_rows
            ]

        result[step_num] = {
            "content": output["content"] if output else "",
            "events": events,  # Chronological events (text + tool_call interleaved)
            "tool_calls": tool_calls,  # Fallback for old data
        }

    return {"steps": result}


@router.get("/{pipeline_id}/steps/{step_number}/output")
async def get_step_output(pipeline_id: str, step_number: int):
    """Get the output for a specific step, including tool calls."""
    db = await get_db()

    step = await db.fetchone("""
        SELECT id FROM pipeline_steps
        WHERE pipeline_id = ? AND step_number = ?
    """, (pipeline_id, step_number))

    if not step:
        raise HTTPException(status_code=404, detail="Step not found")

    outputs = await db.fetchall("""
        SELECT * FROM step_outputs
        WHERE step_id = ?
        ORDER BY created_at DESC
    """, (step["id"],))

    # Also get tool calls for this step
    tool_calls = await db.fetchall("""
        SELECT tool_name, arguments, created_at
        FROM tool_calls
        WHERE step_id = ?
        ORDER BY created_at ASC
    """, (step["id"],))

    return {
        "outputs": outputs,
        "tool_calls": [
            {"tool": tc["tool_name"], "arguments": tc["arguments"], "timestamp": tc["created_at"]}
            for tc in tool_calls
        ]
    }


@router.post("/{pipeline_id}/start")
async def start_pipeline(pipeline_id: str, background_tasks: BackgroundTasks):
    """Start pipeline execution."""
    db = await get_db()

    pipeline = await db.fetchone(
        "SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)
    )
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    if pipeline["status"] not in ("pending", "paused", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"Pipeline cannot be started from status: {pipeline['status']}"
        )

    # Update pipeline status
    now = datetime.utcnow().isoformat()
    await db.execute("""
        UPDATE pipelines
        SET status = 'running', started_at = COALESCE(started_at, ?), pause_requested = FALSE
        WHERE id = ?
    """, (now, pipeline_id))

    # Update current step (reset error if retrying from failed)
    await db.execute("""
        UPDATE pipeline_steps
        SET status = 'running', started_at = ?, error_message = NULL
        WHERE pipeline_id = ? AND step_number = ?
    """, (now, pipeline_id, pipeline["current_step"]))

    await db.commit()

    # Start execution in background
    engine = PipelineEngine(pipeline_id)
    background_tasks.add_task(engine.run)

    return {"status": "started", "pipeline_id": pipeline_id}


@router.post("/{pipeline_id}/pause")
async def pause_pipeline(pipeline_id: str):
    """Pause pipeline execution."""
    db = await get_db()

    pipeline = await db.fetchone(
        "SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)
    )
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    if pipeline["status"] != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Pipeline cannot be paused from status: {pipeline['status']}"
        )

    # Set pause request flag
    await db.execute("""
        UPDATE pipelines
        SET pause_requested = TRUE
        WHERE id = ?
    """, (pipeline_id,))
    await db.commit()

    return {"status": "pause_requested", "pipeline_id": pipeline_id}


@router.post("/{pipeline_id}/resume")
async def resume_pipeline(pipeline_id: str, background_tasks: BackgroundTasks):
    """Resume paused pipeline."""
    db = await get_db()

    pipeline = await db.fetchone(
        "SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)
    )
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    if pipeline["status"] != "paused":
        raise HTTPException(
            status_code=400,
            detail=f"Pipeline cannot be resumed from status: {pipeline['status']}"
        )

    now = datetime.utcnow().isoformat()
    await db.execute("""
        UPDATE pipelines
        SET status = 'running', pause_requested = FALSE
        WHERE id = ?
    """, (pipeline_id,))

    await db.execute("""
        UPDATE pipeline_steps
        SET status = 'running'
        WHERE pipeline_id = ? AND step_number = ?
    """, (pipeline_id, pipeline["current_step"]))

    await db.commit()

    # Resume execution
    engine = PipelineEngine(pipeline_id)
    background_tasks.add_task(engine.run)

    return {"status": "resumed", "pipeline_id": pipeline_id}


@router.post("/{pipeline_id}/restart")
async def restart_pipeline(
    pipeline_id: str,
    request: RestartRequest,
    background_tasks: BackgroundTasks
):
    """Restart pipeline from a specific step."""
    db = await get_db()

    pipeline = await db.fetchone(
        "SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)
    )
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    if request.from_step < 1 or request.from_step > 8:
        raise HTTPException(status_code=400, detail="Invalid step number")

    now = datetime.utcnow().isoformat()

    # Reset pipeline
    await db.execute("""
        UPDATE pipelines
        SET status = 'running', current_step = ?, pause_requested = FALSE
        WHERE id = ?
    """, (request.from_step, pipeline_id))

    # Reset steps from_step onwards
    await db.execute("""
        UPDATE pipeline_steps
        SET status = 'pending', started_at = NULL, completed_at = NULL,
            tokens_used = 0, cost = 0, error_message = NULL
        WHERE pipeline_id = ? AND step_number >= ?
    """, (pipeline_id, request.from_step))

    # Set current step to running
    await db.execute("""
        UPDATE pipeline_steps
        SET status = 'running', started_at = ?
        WHERE pipeline_id = ? AND step_number = ?
    """, (now, pipeline_id, request.from_step))

    # Delete outputs for reset steps
    step_ids = await db.fetchall("""
        SELECT id FROM pipeline_steps
        WHERE pipeline_id = ? AND step_number >= ?
    """, (pipeline_id, request.from_step))

    for step in step_ids:
        await db.execute(
            "DELETE FROM step_outputs WHERE step_id = ?", (step["id"],)
        )

    await db.commit()

    # Start execution
    engine = PipelineEngine(pipeline_id, guidance=request.guidance)
    background_tasks.add_task(engine.run)

    return {
        "status": "restarted",
        "pipeline_id": pipeline_id,
        "from_step": request.from_step
    }


@router.post("/{pipeline_id}/steps/{step_number}/feedback")
async def provide_step_feedback(
    pipeline_id: str,
    step_number: int,
    request: FeedbackRequest,
    background_tasks: BackgroundTasks
):
    """Provide feedback on a step, triggering re-execution."""
    db = await get_db()

    step = await db.fetchone("""
        SELECT * FROM pipeline_steps
        WHERE pipeline_id = ? AND step_number = ?
    """, (pipeline_id, step_number))

    if not step:
        raise HTTPException(status_code=404, detail="Step not found")

    now = datetime.utcnow().isoformat()

    # Save feedback
    feedback_id = generate_id()
    await db.execute("""
        INSERT INTO step_feedback (id, step_id, feedback_text, created_at)
        VALUES (?, ?, ?, ?)
    """, (feedback_id, step["id"], request.feedback, now))

    # Archive current output
    outputs = await db.fetchall(
        "SELECT * FROM step_outputs WHERE step_id = ?", (step["id"],)
    )
    for output in outputs:
        await db.execute("""
            INSERT INTO step_output_history
            (id, step_id, attempt_number, output_type, content, content_json, feedback_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            generate_id(),
            step["id"],
            step["retry_count"] + 1,
            output["output_type"],
            output["content"],
            output["content_json"],
            feedback_id,
            now
        ))

    # Delete current outputs
    await db.execute("DELETE FROM step_outputs WHERE step_id = ?", (step["id"],))

    # Reset step
    await db.execute("""
        UPDATE pipeline_steps
        SET status = 'running', started_at = ?, retry_count = retry_count + 1,
            last_feedback_id = ?, error_message = NULL
        WHERE id = ?
    """, (now, feedback_id, step["id"]))

    # Reset subsequent steps
    await db.execute("""
        UPDATE pipeline_steps
        SET status = 'pending', started_at = NULL, completed_at = NULL,
            tokens_used = 0, cost = 0, error_message = NULL
        WHERE pipeline_id = ? AND step_number > ?
    """, (pipeline_id, step_number))

    # Update pipeline
    await db.execute("""
        UPDATE pipelines
        SET status = 'running', current_step = ?, pause_requested = FALSE
        WHERE id = ?
    """, (step_number, pipeline_id))

    await db.commit()

    # Re-run step with feedback
    engine = PipelineEngine(pipeline_id, feedback=request.feedback)
    background_tasks.add_task(engine.run)

    return {
        "status": "step_restarted",
        "step": step_number,
        "with_feedback": True
    }


@router.get("/{pipeline_id}/steps/{step_number}/history")
async def get_step_history(pipeline_id: str, step_number: int):
    """Get step revision history."""
    db = await get_db()

    step = await db.fetchone("""
        SELECT id FROM pipeline_steps
        WHERE pipeline_id = ? AND step_number = ?
    """, (pipeline_id, step_number))

    if not step:
        raise HTTPException(status_code=404, detail="Step not found")

    history = await db.fetchall("""
        SELECT
            h.*,
            f.feedback_text
        FROM step_output_history h
        LEFT JOIN step_feedback f ON h.feedback_id = f.id
        WHERE h.step_id = ?
        ORDER BY h.attempt_number DESC
    """, (step["id"],))

    return {"history": history}


@router.post("/{pipeline_id}/provide-input")
async def provide_pipeline_input(
    pipeline_id: str,
    request: ProvideInputRequest,
    background_tasks: BackgroundTasks
):
    """Provide user input for a pipeline waiting on input (e.g., worktree setup commands).

    This endpoint is used when the pipeline is in 'needs_user_input' status.
    Currently supports:
    - input_type="setup_commands": Provide setup commands for worktree repos
    """
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"[PROVIDE-INPUT] Received input for pipeline {pipeline_id}, type={request.input_type}")

    db = await get_db()

    pipeline = await db.fetchone(
        "SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)
    )
    if not pipeline:
        logger.error(f"[PROVIDE-INPUT] Pipeline {pipeline_id} not found")
        raise HTTPException(status_code=404, detail="Pipeline not found")

    logger.info(f"[PROVIDE-INPUT] Pipeline status: {pipeline['status']}")

    if pipeline["status"] != "needs_user_input":
        raise HTTPException(
            status_code=400,
            detail=f"Pipeline is not waiting for input. Current status: {pipeline['status']}"
        )

    if request.input_type == "setup_commands":
        if not settings.worktree_enabled:
            raise HTTPException(
                status_code=400,
                detail="Worktree feature is not enabled"
            )

        # Find the worktree session for this pipeline
        session = await db.fetchone(
            "SELECT * FROM worktree_sessions WHERE pipeline_id = ?",
            (pipeline_id,)
        )
        if not session:
            logger.error(f"[PROVIDE-INPUT] No worktree session found for pipeline {pipeline_id}")
            raise HTTPException(
                status_code=404,
                detail="No worktree session found for this pipeline"
            )

        logger.info(f"[PROVIDE-INPUT] Found worktree session {session['id']}, status={session['status']}")
        logger.info(f"[PROVIDE-INPUT] Setup commands: {request.data}")

        # Run setup and resume pipeline in background (don't block HTTP response)
        async def run_setup_and_resume():
            import logging
            bg_logger = logging.getLogger(__name__)
            try:
                bg_logger.info(f"[PROVIDE-INPUT-BG] Starting setup for session {session['id']}")
                manager = WorktreeManager()
                result = await manager.provide_user_input(session["id"], request.data)
                bg_logger.info(f"[PROVIDE-INPUT-BG] Setup result: {result}")

                if result.success:
                    bg_logger.info(f"[PROVIDE-INPUT-BG] Setup succeeded, resuming pipeline")
                    engine = PipelineEngine(pipeline_id)
                    await engine.resume_after_user_input()
                else:
                    bg_logger.error(f"[PROVIDE-INPUT-BG] Setup failed, not resuming pipeline")
                    # Broadcast failure
                    from websocket.manager import broadcast_message
                    await broadcast_message({
                        "type": "worktree_setup_failed",
                        "pipeline_id": pipeline_id,
                        "message": "Setup commands failed. Check logs for details."
                    })
            except Exception as e:
                bg_logger.exception(f"[PROVIDE-INPUT-BG] Error: {e}")

        background_tasks.add_task(run_setup_and_resume)
        logger.info(f"[PROVIDE-INPUT] Added setup task to background, returning immediately")

        return {
            "status": "input_received",
            "pipeline_id": pipeline_id,
            "input_type": request.input_type,
            "message": "Setup commands received. Running setup in background..."
        }

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown input type: {request.input_type}"
        )
