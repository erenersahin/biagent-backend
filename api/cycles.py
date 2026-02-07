"""
Cycles API Router

Endpoints for managing cycle types and phases.
"""

from fastapi import APIRouter, HTTPException
from typing import List, Optional
from pydantic import BaseModel

from db import get_db, generate_id
from services.cycle_detector import CycleDetector, CycleType


router = APIRouter()


class CyclePhaseResponse(BaseModel):
    """Cycle phase response model."""
    id: str
    step_number: int
    name: str
    description: Optional[str] = None
    is_enabled: bool


class CycleTypeResponse(BaseModel):
    """Cycle type response model."""
    id: str
    name: str
    display_name: str
    description: Optional[str] = None
    icon: Optional[str] = None


class CycleTypeWithPhasesResponse(BaseModel):
    """Cycle type with phases response model."""
    id: str
    name: str
    display_name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    phases: List[CyclePhaseResponse]


# Default cycle type definitions
DEFAULT_CYCLE_TYPES = [
    {
        "id": "cycle_backend",
        "name": "backend",
        "display_name": "Backend",
        "description": "Full backend development cycle with all 8 steps",
        "icon": "server",
    },
    {
        "id": "cycle_frontend",
        "name": "frontend",
        "display_name": "Frontend",
        "description": "Frontend development cycle focused on UI/UX",
        "icon": "layout",
    },
    {
        "id": "cycle_fullstack",
        "name": "fullstack",
        "display_name": "Fullstack",
        "description": "Full stack development cycle covering both frontend and backend",
        "icon": "layers",
    },
    {
        "id": "cycle_spike",
        "name": "spike",
        "display_name": "Spike",
        "description": "Quick investigation/research cycle for unknowns",
        "icon": "search",
    },
    {
        "id": "cycle_oncall_bug",
        "name": "oncall_bug",
        "display_name": "Oncall Bug",
        "description": "Urgent bug fix cycle with streamlined steps",
        "icon": "alert-triangle",
    },
]

# Default phase definitions for each cycle type
# All cycle types have 8 phases but some may be disabled
DEFAULT_PHASES = {
    "backend": [
        {"step_number": 1, "name": "Context", "description": "Gather ticket details and codebase context", "is_enabled": True},
        {"step_number": 2, "name": "Risk", "description": "Analyze blockers and dependencies", "is_enabled": True},
        {"step_number": 3, "name": "Planning", "description": "Create implementation plan", "is_enabled": True},
        {"step_number": 4, "name": "Coding", "description": "Implement on sandbox branch", "is_enabled": True},
        {"step_number": 5, "name": "Testing", "description": "Write and run tests", "is_enabled": True},
        {"step_number": 6, "name": "Docs", "description": "Update documentation", "is_enabled": True},
        {"step_number": 7, "name": "PR", "description": "Create pull request", "is_enabled": True},
        {"step_number": 8, "name": "Review", "description": "Handle PR feedback", "is_enabled": True},
    ],
    "frontend": [
        {"step_number": 1, "name": "Context", "description": "Gather UI requirements and design specs", "is_enabled": True},
        {"step_number": 2, "name": "Risk", "description": "Analyze design and UX risks", "is_enabled": True},
        {"step_number": 3, "name": "Planning", "description": "Create component plan", "is_enabled": True},
        {"step_number": 4, "name": "Coding", "description": "Implement UI components", "is_enabled": True},
        {"step_number": 5, "name": "Testing", "description": "Write component tests", "is_enabled": True},
        {"step_number": 6, "name": "Docs", "description": "Update storybook/docs", "is_enabled": False},
        {"step_number": 7, "name": "PR", "description": "Create pull request", "is_enabled": True},
        {"step_number": 8, "name": "Review", "description": "Handle PR feedback", "is_enabled": True},
    ],
    "fullstack": [
        {"step_number": 1, "name": "Context", "description": "Gather full stack requirements", "is_enabled": True},
        {"step_number": 2, "name": "Risk", "description": "Analyze integration risks", "is_enabled": True},
        {"step_number": 3, "name": "Planning", "description": "Create full stack plan", "is_enabled": True},
        {"step_number": 4, "name": "Coding", "description": "Implement frontend and backend", "is_enabled": True},
        {"step_number": 5, "name": "Testing", "description": "Write integration tests", "is_enabled": True},
        {"step_number": 6, "name": "Docs", "description": "Update API and UI docs", "is_enabled": True},
        {"step_number": 7, "name": "PR", "description": "Create pull request(s)", "is_enabled": True},
        {"step_number": 8, "name": "Review", "description": "Handle PR feedback", "is_enabled": True},
    ],
    "spike": [
        {"step_number": 1, "name": "Context", "description": "Define research question", "is_enabled": True},
        {"step_number": 2, "name": "Risk", "description": "Identify unknowns", "is_enabled": True},
        {"step_number": 3, "name": "Planning", "description": "Create investigation plan", "is_enabled": True},
        {"step_number": 4, "name": "Coding", "description": "Prototype/POC", "is_enabled": True},
        {"step_number": 5, "name": "Testing", "description": "Validate findings", "is_enabled": False},
        {"step_number": 6, "name": "Docs", "description": "Document findings", "is_enabled": True},
        {"step_number": 7, "name": "PR", "description": "Create RFC/proposal", "is_enabled": False},
        {"step_number": 8, "name": "Review", "description": "Team review", "is_enabled": False},
    ],
    "oncall_bug": [
        {"step_number": 1, "name": "Context", "description": "Reproduce and understand bug", "is_enabled": True},
        {"step_number": 2, "name": "Risk", "description": "Assess impact and urgency", "is_enabled": True},
        {"step_number": 3, "name": "Planning", "description": "Plan fix approach", "is_enabled": False},
        {"step_number": 4, "name": "Coding", "description": "Implement fix", "is_enabled": True},
        {"step_number": 5, "name": "Testing", "description": "Test fix", "is_enabled": True},
        {"step_number": 6, "name": "Docs", "description": "Update runbook", "is_enabled": False},
        {"step_number": 7, "name": "PR", "description": "Create hotfix PR", "is_enabled": True},
        {"step_number": 8, "name": "Review", "description": "Quick review", "is_enabled": True},
    ],
}


async def seed_cycle_types():
    """Seed default cycle types and phases into database."""
    db = await get_db()

    for cycle_type in DEFAULT_CYCLE_TYPES:
        # Check if cycle type already exists
        existing = await db.fetchone(
            "SELECT id FROM cycle_types WHERE name = ?",
            (cycle_type["name"],)
        )
        if existing:
            continue

        # Insert cycle type
        await db.execute("""
            INSERT INTO cycle_types (id, name, display_name, description, icon)
            VALUES (?, ?, ?, ?, ?)
        """, (
            cycle_type["id"],
            cycle_type["name"],
            cycle_type["display_name"],
            cycle_type["description"],
            cycle_type["icon"],
        ))

        # Insert phases for this cycle type
        phases = DEFAULT_PHASES.get(cycle_type["name"], [])
        for phase in phases:
            phase_id = generate_id()
            await db.execute("""
                INSERT INTO cycle_phases (id, cycle_type_id, step_number, name, description, is_enabled)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                phase_id,
                cycle_type["id"],
                phase["step_number"],
                phase["name"],
                phase["description"],
                phase["is_enabled"],
            ))

    await db.commit()


@router.get("", response_model=List[CycleTypeResponse])
async def get_cycle_types():
    """Get all available cycle types."""
    db = await get_db()

    # Seed if empty
    count = await db.fetchone("SELECT COUNT(*) as count FROM cycle_types")
    if not count or count["count"] == 0:
        await seed_cycle_types()

    types = await db.fetchall("""
        SELECT id, name, display_name, description, icon
        FROM cycle_types
        ORDER BY name
    """)

    return [CycleTypeResponse(**t) for t in types]


@router.get("/{cycle_type}", response_model=CycleTypeWithPhasesResponse)
async def get_cycle_type_with_phases(cycle_type: str):
    """Get a specific cycle type with its phases."""
    db = await get_db()

    # Seed if empty
    count = await db.fetchone("SELECT COUNT(*) as count FROM cycle_types")
    if not count or count["count"] == 0:
        await seed_cycle_types()

    # Get cycle type by name
    ct = await db.fetchone("""
        SELECT id, name, display_name, description, icon
        FROM cycle_types
        WHERE name = ?
    """, (cycle_type,))

    if not ct:
        raise HTTPException(status_code=404, detail=f"Cycle type '{cycle_type}' not found")

    # Get phases for this cycle type
    phases = await db.fetchall("""
        SELECT id, step_number, name, description, is_enabled
        FROM cycle_phases
        WHERE cycle_type_id = ?
        ORDER BY step_number
    """, (ct["id"],))

    return CycleTypeWithPhasesResponse(
        **dict(ct),
        phases=[CyclePhaseResponse(**p) for p in phases]
    )


@router.get("/{cycle_type}/phases", response_model=List[CyclePhaseResponse])
async def get_cycle_phases(cycle_type: str):
    """Get phases for a specific cycle type."""
    db = await get_db()

    # Seed if empty
    count = await db.fetchone("SELECT COUNT(*) as count FROM cycle_types")
    if not count or count["count"] == 0:
        await seed_cycle_types()

    # Get cycle type by name
    ct = await db.fetchone(
        "SELECT id FROM cycle_types WHERE name = ?",
        (cycle_type,)
    )

    if not ct:
        raise HTTPException(status_code=404, detail=f"Cycle type '{cycle_type}' not found")

    phases = await db.fetchall("""
        SELECT id, step_number, name, description, is_enabled
        FROM cycle_phases
        WHERE cycle_type_id = ?
        ORDER BY step_number
    """, (ct["id"],))

    return [CyclePhaseResponse(**p) for p in phases]


class CycleDetectionRequest(BaseModel):
    """Request for cycle type detection."""
    ticket_key: str
    summary: str
    description: Optional[str] = None
    labels: Optional[List[str]] = None
    project_key: Optional[str] = None
    issue_type: Optional[str] = None
    affected_repos: Optional[List[str]] = None


class CycleDetectionResponse(BaseModel):
    """Response for cycle type detection."""
    cycle_type: str
    confidence: str
    reason: str
    suggested_override: Optional[str] = None


@router.post("/detect", response_model=CycleDetectionResponse)
async def detect_cycle_type(request: CycleDetectionRequest):
    """
    Detect the appropriate cycle type for a ticket.

    Analyzes labels, project key, issue type, and content to determine
    the best cycle type (backend, frontend, fullstack, spike, oncall_bug).
    """
    detector = CycleDetector()
    result = detector.detect(
        ticket_key=request.ticket_key,
        summary=request.summary,
        description=request.description,
        labels=request.labels,
        project_key=request.project_key,
        issue_type=request.issue_type,
        affected_repos=request.affected_repos,
    )

    return CycleDetectionResponse(
        cycle_type=result.cycle_type.value,
        confidence=result.confidence,
        reason=result.reason,
        suggested_override=result.suggested_override.value if result.suggested_override else None,
    )
