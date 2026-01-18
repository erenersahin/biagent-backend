"""
Worktrees API Router

Endpoints for managing git worktree sessions.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime

from db import get_db
from services.worktree_manager import WorktreeManager, WorktreeSessionStatus


router = APIRouter()


class WorktreeRepoResponse(BaseModel):
    """Response model for a single repo worktree."""
    id: str
    repo_name: str
    repo_path: str
    worktree_path: str
    branch_name: str
    status: str
    setup_commands: Optional[List[str]] = None
    pr_url: Optional[str] = None
    pr_merged: bool = False


class WorktreeSessionResponse(BaseModel):
    """Response model for a worktree session."""
    id: str
    pipeline_id: str
    ticket_key: str
    status: str
    base_path: str
    repos: List[WorktreeRepoResponse]
    created_at: Optional[str] = None
    ready_at: Optional[str] = None
    error_message: Optional[str] = None
    user_input_request: Optional[Dict] = None


class WorktreeListResponse(BaseModel):
    """Response model for list of worktree sessions."""
    sessions: List[WorktreeSessionResponse]
    total: int


class ProvideInputRequest(BaseModel):
    """Request model for providing user input."""
    input_type: str  # "setup_commands"
    data: Dict[str, List[str]]  # repo_name -> commands


@router.get("", response_model=WorktreeListResponse)
async def list_worktrees(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """List all worktree sessions, optionally filtered by status."""
    db = await get_db()

    if status:
        sessions = await db.fetchall("""
            SELECT * FROM worktree_sessions
            WHERE status = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (status, limit, offset))
        count_result = await db.fetchone(
            "SELECT COUNT(*) as count FROM worktree_sessions WHERE status = ?",
            (status,)
        )
    else:
        sessions = await db.fetchall("""
            SELECT * FROM worktree_sessions
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))
        count_result = await db.fetchone(
            "SELECT COUNT(*) as count FROM worktree_sessions"
        )

    result = []
    for session in sessions:
        repos = await db.fetchall("""
            SELECT * FROM worktree_repos WHERE session_id = ?
        """, (session["id"],))

        result.append(WorktreeSessionResponse(
            id=session["id"],
            pipeline_id=session["pipeline_id"],
            ticket_key=session["ticket_key"],
            status=session["status"],
            base_path=session["base_path"],
            repos=[WorktreeRepoResponse(
                id=r["id"],
                repo_name=r["repo_name"],
                repo_path=r["repo_path"],
                worktree_path=r["worktree_path"],
                branch_name=r["branch_name"],
                status=r["status"],
                pr_url=r["pr_url"],
                pr_merged=bool(r["pr_merged"]),
            ) for r in repos],
            created_at=session["created_at"],
            ready_at=session["ready_at"],
            error_message=session["error_message"],
        ))

    return WorktreeListResponse(
        sessions=result,
        total=count_result["count"]
    )


@router.get("/{session_id}", response_model=WorktreeSessionResponse)
async def get_worktree_session(session_id: str):
    """Get a specific worktree session with all repos."""
    db = await get_db()

    session = await db.fetchone("""
        SELECT * FROM worktree_sessions WHERE id = ?
    """, (session_id,))

    if not session:
        raise HTTPException(status_code=404, detail="Worktree session not found")

    repos = await db.fetchall("""
        SELECT * FROM worktree_repos WHERE session_id = ?
    """, (session_id,))

    return WorktreeSessionResponse(
        id=session["id"],
        pipeline_id=session["pipeline_id"],
        ticket_key=session["ticket_key"],
        status=session["status"],
        base_path=session["base_path"],
        repos=[WorktreeRepoResponse(
            id=r["id"],
            repo_name=r["repo_name"],
            repo_path=r["repo_path"],
            worktree_path=r["worktree_path"],
            branch_name=r["branch_name"],
            status=r["status"],
            pr_url=r["pr_url"],
            pr_merged=bool(r["pr_merged"]),
        ) for r in repos],
        created_at=session["created_at"],
        ready_at=session["ready_at"],
        error_message=session["error_message"],
    )


@router.get("/by-pipeline/{pipeline_id}", response_model=WorktreeSessionResponse)
async def get_worktree_by_pipeline(pipeline_id: str):
    """Get worktree session for a specific pipeline."""
    db = await get_db()

    session = await db.fetchone("""
        SELECT * FROM worktree_sessions WHERE pipeline_id = ?
    """, (pipeline_id,))

    if not session:
        raise HTTPException(status_code=404, detail="No worktree session for this pipeline")

    repos = await db.fetchall("""
        SELECT * FROM worktree_repos WHERE session_id = ?
    """, (session["id"],))

    return WorktreeSessionResponse(
        id=session["id"],
        pipeline_id=session["pipeline_id"],
        ticket_key=session["ticket_key"],
        status=session["status"],
        base_path=session["base_path"],
        repos=[WorktreeRepoResponse(
            id=r["id"],
            repo_name=r["repo_name"],
            repo_path=r["repo_path"],
            worktree_path=r["worktree_path"],
            branch_name=r["branch_name"],
            status=r["status"],
            pr_url=r["pr_url"],
            pr_merged=bool(r["pr_merged"]),
        ) for r in repos],
        created_at=session["created_at"],
        ready_at=session["ready_at"],
        error_message=session["error_message"],
    )


@router.post("/{session_id}/cleanup")
async def cleanup_worktree(session_id: str, background_tasks: BackgroundTasks, force: bool = False):
    """Manually trigger worktree cleanup."""
    db = await get_db()

    session = await db.fetchone("""
        SELECT * FROM worktree_sessions WHERE id = ?
    """, (session_id,))

    if not session:
        raise HTTPException(status_code=404, detail="Worktree session not found")

    if session["status"] == "cleaned":
        raise HTTPException(status_code=400, detail="Worktree session already cleaned")

    manager = WorktreeManager()
    background_tasks.add_task(manager.cleanup_session, session_id, force)

    return {
        "status": "cleanup_scheduled",
        "session_id": session_id
    }


@router.post("/cleanup-stale")
async def cleanup_stale_worktrees(background_tasks: BackgroundTasks):
    """Clean up all stale worktrees (those with all PRs merged)."""
    db = await get_db()

    # Find sessions where all PRs are merged
    sessions = await db.fetchall("""
        SELECT ws.* FROM worktree_sessions ws
        WHERE ws.status = 'ready'
        AND NOT EXISTS (
            SELECT 1 FROM worktree_repos wr
            WHERE wr.session_id = ws.id
            AND wr.pr_merged = FALSE
            AND wr.pr_url IS NOT NULL
        )
    """)

    manager = WorktreeManager()
    for session in sessions:
        background_tasks.add_task(manager.cleanup_session, session["id"], True)

    return {
        "status": "cleanup_scheduled",
        "count": len(sessions)
    }


@router.get("/repos/detect")
async def detect_repos():
    """Detect all git repositories in the base path."""
    manager = WorktreeManager()
    repos = await manager.detect_repos()

    return {
        "repos": repos,
        "base_path": str(manager.base_path)
    }
