"""
Waitlist API Router

Endpoint for handling waitlist form submissions.
"""

import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional, List

from db import get_db, generate_id


router = APIRouter()


class WaitlistRequest(BaseModel):
    """Request model for waitlist signup."""
    email: EmailStr
    name: Optional[str] = None
    role: Optional[str] = None  # 'developer', 'lead', 'manager', 'founder', 'other'
    use_cases: Optional[List[str]] = None  # Selected use case IDs
    created_at: str  # UTC timestamp from frontend (ISO format)


class WaitlistResponse(BaseModel):
    """Response model for waitlist signup."""
    id: str
    email: str
    message: str


class WaitlistEntry(BaseModel):
    """Full waitlist entry for admin viewing."""
    id: str
    email: str
    name: Optional[str] = None
    role: Optional[str] = None
    use_cases: Optional[List[str]] = None
    created_at: str


class WaitlistListResponse(BaseModel):
    """Response model for listing waitlist entries."""
    entries: List[WaitlistEntry]
    total: int


@router.post("", response_model=WaitlistResponse)
async def join_waitlist(request: WaitlistRequest):
    """Add a new entry to the waitlist."""
    db = await get_db()

    # Check if email already exists
    existing = await db.fetchone(
        "SELECT id FROM waitlist WHERE email = ?",
        (request.email,)
    )

    if existing:
        raise HTTPException(
            status_code=409,
            detail="This email is already on the waitlist"
        )

    # Insert new waitlist entry
    entry_id = generate_id()
    use_cases_json = json.dumps(request.use_cases) if request.use_cases else None

    await db.execute(
        """
        INSERT INTO waitlist (id, email, name, role, use_cases, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (entry_id, request.email, request.name, request.role, use_cases_json, request.created_at)
    )
    await db.commit()

    return WaitlistResponse(
        id=entry_id,
        email=request.email,
        message="Successfully joined the waitlist"
    )


@router.get("", response_model=WaitlistListResponse)
async def list_waitlist(limit: int = 100, offset: int = 0):
    """List all waitlist entries (admin endpoint)."""
    db = await get_db()

    entries = await db.fetchall(
        """
        SELECT * FROM waitlist
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset)
    )

    count_result = await db.fetchone("SELECT COUNT(*) as count FROM waitlist")

    return WaitlistListResponse(
        entries=[
            WaitlistEntry(
                id=e["id"],
                email=e["email"],
                name=e["name"],
                role=e["role"],
                use_cases=json.loads(e["use_cases"]) if e["use_cases"] else None,
                created_at=e["created_at"],
            )
            for e in entries
        ],
        total=count_result["count"],
    )
