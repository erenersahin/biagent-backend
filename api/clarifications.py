"""
Clarifications API Router

Endpoints for managing agent clarification requests.
When agents encounter ambiguous situations, they can request clarification
from users via multiple-choice questions.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime
import json

from db import get_db, generate_id
from websocket.manager import broadcast_message


router = APIRouter()


class ClarificationCreate(BaseModel):
    """Request to create a clarification (typically from agent)."""
    step_id: str
    pipeline_id: str
    question: str
    options: List[str]  # 2-4 options
    context: Optional[str] = None


class ClarificationAnswer(BaseModel):
    """Answer to a clarification question."""
    selected_option: Optional[int] = None  # Index of selected option (0-based)
    custom_answer: Optional[str] = None  # Free text if "Other" selected


class ClarificationResponse(BaseModel):
    """Clarification response model."""
    id: str
    step_id: str
    pipeline_id: str
    question: str
    options: List[str]
    selected_option: Optional[int] = None
    custom_answer: Optional[str] = None
    context: Optional[str] = None
    status: str
    created_at: str
    answered_at: Optional[str] = None


@router.post("", response_model=ClarificationResponse)
async def create_clarification(request: ClarificationCreate):
    """Create a new clarification request.

    This is called by agents when they need user input.
    The pipeline status will be updated to 'waiting' and the step to 'waiting'.
    """
    db = await get_db()

    # Validate options count
    if len(request.options) < 2 or len(request.options) > 4:
        raise HTTPException(
            status_code=400,
            detail="Clarification must have 2-4 options"
        )

    # Verify step exists
    step = await db.fetchone(
        "SELECT * FROM pipeline_steps WHERE id = ?",
        (request.step_id,)
    )
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")

    now = datetime.utcnow().isoformat()
    clarification_id = generate_id()

    # Create clarification
    await db.execute("""
        INSERT INTO clarifications (id, step_id, pipeline_id, question, options, context, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (
        clarification_id,
        request.step_id,
        request.pipeline_id,
        request.question,
        json.dumps(request.options),
        request.context,
        now
    ))

    # Update step status to waiting
    await db.execute("""
        UPDATE pipeline_steps
        SET status = 'waiting', waiting_for = 'clarification'
        WHERE id = ?
    """, (request.step_id,))

    # Update pipeline status
    await db.execute("""
        UPDATE pipelines
        SET status = 'waiting_for_review'
        WHERE id = ?
    """, (request.pipeline_id,))

    await db.commit()

    # Broadcast clarification request
    await broadcast_message({
        "type": "clarification_requested",
        "pipeline_id": request.pipeline_id,
        "step": step["step_number"],
        "clarification_id": clarification_id,
        "question": request.question,
        "options": request.options,
        "context": request.context
    })

    return ClarificationResponse(
        id=clarification_id,
        step_id=request.step_id,
        pipeline_id=request.pipeline_id,
        question=request.question,
        options=request.options,
        context=request.context,
        status="pending",
        created_at=now
    )


@router.get("/{clarification_id}", response_model=ClarificationResponse)
async def get_clarification(clarification_id: str):
    """Get a clarification by ID."""
    db = await get_db()

    clarification = await db.fetchone(
        "SELECT * FROM clarifications WHERE id = ?",
        (clarification_id,)
    )
    if not clarification:
        raise HTTPException(status_code=404, detail="Clarification not found")

    return ClarificationResponse(
        id=clarification["id"],
        step_id=clarification["step_id"],
        pipeline_id=clarification["pipeline_id"],
        question=clarification["question"],
        options=json.loads(clarification["options"]),
        selected_option=clarification["selected_option"],
        custom_answer=clarification["custom_answer"],
        context=clarification["context"],
        status=clarification["status"],
        created_at=clarification["created_at"],
        answered_at=clarification["answered_at"]
    )


@router.get("/pipeline/{pipeline_id}")
async def get_pipeline_clarifications(pipeline_id: str, status: Optional[str] = None):
    """Get all clarifications for a pipeline, optionally filtered by status."""
    db = await get_db()

    if status:
        clarifications = await db.fetchall("""
            SELECT * FROM clarifications
            WHERE pipeline_id = ? AND status = ?
            ORDER BY created_at DESC
        """, (pipeline_id, status))
    else:
        clarifications = await db.fetchall("""
            SELECT * FROM clarifications
            WHERE pipeline_id = ?
            ORDER BY created_at DESC
        """, (pipeline_id,))

    return {
        "clarifications": [
            ClarificationResponse(
                id=c["id"],
                step_id=c["step_id"],
                pipeline_id=c["pipeline_id"],
                question=c["question"],
                options=json.loads(c["options"]),
                selected_option=c["selected_option"],
                custom_answer=c["custom_answer"],
                context=c["context"],
                status=c["status"],
                created_at=c["created_at"],
                answered_at=c["answered_at"]
            )
            for c in clarifications
        ]
    }


@router.post("/{clarification_id}/answer")
async def answer_clarification(
    clarification_id: str,
    request: ClarificationAnswer,
    background_tasks: BackgroundTasks
):
    """Answer a clarification question.

    The answer will be recorded and the pipeline will resume.
    """
    db = await get_db()

    clarification = await db.fetchone(
        "SELECT * FROM clarifications WHERE id = ?",
        (clarification_id,)
    )
    if not clarification:
        raise HTTPException(status_code=404, detail="Clarification not found")

    if clarification["status"] != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Clarification already {clarification['status']}"
        )

    # Validate answer
    options = json.loads(clarification["options"])
    if request.selected_option is not None:
        if request.selected_option < 0 or request.selected_option >= len(options):
            raise HTTPException(status_code=400, detail="Invalid option index")

    now = datetime.utcnow().isoformat()

    # Update clarification
    await db.execute("""
        UPDATE clarifications
        SET selected_option = ?, custom_answer = ?, status = 'answered', answered_at = ?
        WHERE id = ?
    """, (
        request.selected_option,
        request.custom_answer,
        now,
        clarification_id
    ))

    # Get step info
    step = await db.fetchone(
        "SELECT * FROM pipeline_steps WHERE id = ?",
        (clarification["step_id"],)
    )

    # Update step status back to running
    await db.execute("""
        UPDATE pipeline_steps
        SET status = 'running', waiting_for = NULL
        WHERE id = ?
    """, (clarification["step_id"],))

    # Update pipeline status back to running
    await db.execute("""
        UPDATE pipelines
        SET status = 'running'
        WHERE id = ?
    """, (clarification["pipeline_id"],))

    await db.commit()

    # Determine the answer text
    if request.custom_answer:
        answer_text = request.custom_answer
    elif request.selected_option is not None:
        answer_text = options[request.selected_option]
    else:
        answer_text = "No answer provided"

    # Broadcast answer
    await broadcast_message({
        "type": "clarification_answered",
        "pipeline_id": clarification["pipeline_id"],
        "step": step["step_number"] if step else None,
        "clarification_id": clarification_id,
        "selected_option": request.selected_option,
        "answer": answer_text
    })

    # Resume pipeline execution
    from services.pipeline_engine import PipelineEngine
    engine = PipelineEngine(
        clarification["pipeline_id"],
        clarification_answer=answer_text
    )
    background_tasks.add_task(engine.run)

    return {
        "status": "answered",
        "clarification_id": clarification_id,
        "answer": answer_text,
        "pipeline_resuming": True
    }


@router.get("/step/{step_id}/pending")
async def get_pending_clarification(step_id: str):
    """Get the pending clarification for a step (if any)."""
    db = await get_db()

    clarification = await db.fetchone("""
        SELECT * FROM clarifications
        WHERE step_id = ? AND status = 'pending'
        ORDER BY created_at DESC
        LIMIT 1
    """, (step_id,))

    if not clarification:
        return {"clarification": None}

    return {
        "clarification": ClarificationResponse(
            id=clarification["id"],
            step_id=clarification["step_id"],
            pipeline_id=clarification["pipeline_id"],
            question=clarification["question"],
            options=json.loads(clarification["options"]),
            selected_option=clarification["selected_option"],
            custom_answer=clarification["custom_answer"],
            context=clarification["context"],
            status=clarification["status"],
            created_at=clarification["created_at"],
            answered_at=clarification["answered_at"]
        )
    }
