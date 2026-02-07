"""
Cycle Detector Service

Detects the appropriate cycle type for a ticket based on:
- Labels
- Project key
- Issue type
- Content analysis

Supports: backend, frontend, fullstack, spike, oncall_bug
"""

from typing import Optional, List
from dataclasses import dataclass
from enum import Enum


class CycleType(str, Enum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    FULLSTACK = "fullstack"
    SPIKE = "spike"
    ONCALL_BUG = "oncall_bug"


@dataclass
class CycleDetectionResult:
    """Result of cycle type detection."""
    cycle_type: CycleType
    confidence: str  # "high", "medium", "low"
    reason: str
    suggested_override: Optional[CycleType] = None


# Label patterns for each cycle type
SPIKE_LABELS = {"spike", "research", "exploration", "investigate", "poc", "prototype", "experiment"}
BUG_LABELS = {"bug", "incident", "oncall", "urgent", "hotfix", "p0", "p1", "production-issue", "sev1", "sev2"}
FRONTEND_LABELS = {"frontend", "ui", "ux", "design", "css", "react", "component", "figma"}
BACKEND_LABELS = {"backend", "api", "service", "database", "migration", "infra"}

# Project key patterns
FRONTEND_PROJECTS = {"WEB", "FE", "FRONTEND", "UI", "MOBILE", "APP", "CLIENT"}
BACKEND_PROJECTS = {"API", "BE", "BACKEND", "SVC", "SERVICE", "DATA", "INFRA"}

# Issue type patterns
SPIKE_ISSUE_TYPES = {"spike", "research", "exploration", "technical debt"}
BUG_ISSUE_TYPES = {"bug", "incident", "defect", "hotfix"}


class CycleDetector:
    """Detects the appropriate cycle type for a ticket."""

    def detect(
        self,
        ticket_key: str,
        summary: str,
        description: Optional[str] = None,
        labels: Optional[List[str]] = None,
        project_key: Optional[str] = None,
        issue_type: Optional[str] = None,
        affected_repos: Optional[List[str]] = None,
    ) -> CycleDetectionResult:
        """
        Detect the appropriate cycle type for a ticket.

        Priority order:
        1. Explicit labels (spike, bug, etc.)
        2. Issue type
        3. Project key
        4. Multi-repo detection
        5. Content analysis
        6. Default to backend

        Args:
            ticket_key: JIRA ticket key (e.g., "PROJ-123")
            summary: Ticket summary/title
            description: Ticket description
            labels: List of labels on the ticket
            project_key: JIRA project key
            issue_type: JIRA issue type
            affected_repos: List of affected repository names (from context analysis)

        Returns:
            CycleDetectionResult with detected cycle type and reasoning
        """
        labels_set = {l.lower() for l in (labels or [])}
        project_upper = (project_key or "").upper()
        issue_type_lower = (issue_type or "").lower()
        content = f"{summary} {description or ''}".lower()

        # 1. Check for explicit spike indicators
        if labels_set & SPIKE_LABELS or issue_type_lower in SPIKE_ISSUE_TYPES:
            return CycleDetectionResult(
                cycle_type=CycleType.SPIKE,
                confidence="high",
                reason=f"Spike indicators found in labels or issue type",
            )

        # 2. Check for bug/incident indicators
        if labels_set & BUG_LABELS or issue_type_lower in BUG_ISSUE_TYPES:
            return CycleDetectionResult(
                cycle_type=CycleType.ONCALL_BUG,
                confidence="high",
                reason=f"Bug/incident indicators found in labels or issue type",
            )

        # 3. Check project key patterns
        if project_upper in FRONTEND_PROJECTS:
            return CycleDetectionResult(
                cycle_type=CycleType.FRONTEND,
                confidence="high",
                reason=f"Frontend project detected: {project_key}",
            )

        if project_upper in BACKEND_PROJECTS:
            return CycleDetectionResult(
                cycle_type=CycleType.BACKEND,
                confidence="high",
                reason=f"Backend project detected: {project_key}",
            )

        # 4. Multi-repo detection (fullstack)
        if affected_repos and len(affected_repos) > 1:
            return CycleDetectionResult(
                cycle_type=CycleType.FULLSTACK,
                confidence="high",
                reason=f"Multiple repositories affected: {', '.join(affected_repos)}",
            )

        # 5. Check labels for frontend/backend hints
        if labels_set & FRONTEND_LABELS:
            return CycleDetectionResult(
                cycle_type=CycleType.FRONTEND,
                confidence="medium",
                reason="Frontend-related labels detected",
            )

        if labels_set & BACKEND_LABELS:
            return CycleDetectionResult(
                cycle_type=CycleType.BACKEND,
                confidence="medium",
                reason="Backend-related labels detected",
            )

        # 6. Content analysis for keywords
        frontend_keywords = {"ui", "component", "page", "button", "form", "modal",
                           "figma", "design", "css", "style", "layout", "responsive"}
        backend_keywords = {"api", "endpoint", "database", "migration", "service",
                          "query", "schema", "model", "cron", "worker"}
        spike_keywords = {"investigate", "research", "explore", "poc", "prototype",
                        "evaluate", "compare", "analyze"}

        frontend_matches = sum(1 for kw in frontend_keywords if kw in content)
        backend_matches = sum(1 for kw in backend_keywords if kw in content)
        spike_matches = sum(1 for kw in spike_keywords if kw in content)

        if spike_matches >= 2:
            return CycleDetectionResult(
                cycle_type=CycleType.SPIKE,
                confidence="medium",
                reason="Research/investigation keywords detected in content",
            )

        if frontend_matches > backend_matches and frontend_matches >= 2:
            return CycleDetectionResult(
                cycle_type=CycleType.FRONTEND,
                confidence="medium",
                reason="Frontend-related keywords detected in content",
            )

        if backend_matches > frontend_matches and backend_matches >= 2:
            return CycleDetectionResult(
                cycle_type=CycleType.BACKEND,
                confidence="medium",
                reason="Backend-related keywords detected in content",
            )

        if frontend_matches > 0 and backend_matches > 0:
            return CycleDetectionResult(
                cycle_type=CycleType.FULLSTACK,
                confidence="low",
                reason="Mixed frontend and backend keywords detected",
                suggested_override=CycleType.BACKEND,
            )

        # 7. Default to backend
        return CycleDetectionResult(
            cycle_type=CycleType.BACKEND,
            confidence="low",
            reason="No specific indicators found, defaulting to backend",
            suggested_override=None,
        )

    def get_available_cycles(self) -> List[dict]:
        """Get list of available cycle types with metadata."""
        return [
            {
                "type": CycleType.BACKEND.value,
                "name": "Backend",
                "description": "Full backend development cycle with 8 phases",
                "phase_count": 8,
                "best_for": "API, data, and service work",
            },
            {
                "type": CycleType.FRONTEND.value,
                "name": "Frontend",
                "description": "Frontend development cycle focused on UI/UX",
                "phase_count": 7,
                "best_for": "UI work with Figma designs",
            },
            {
                "type": CycleType.FULLSTACK.value,
                "name": "Fullstack",
                "description": "Full stack development covering frontend and backend",
                "phase_count": 8,
                "best_for": "Features spanning multiple repos",
            },
            {
                "type": CycleType.SPIKE.value,
                "name": "Spike",
                "description": "Quick investigation/research cycle",
                "phase_count": 4,
                "best_for": "Research, exploration, and decision-making",
            },
            {
                "type": CycleType.ONCALL_BUG.value,
                "name": "Oncall Bug",
                "description": "Urgent bug fix cycle with streamlined steps",
                "phase_count": 6,
                "best_for": "Production incidents and urgent fixes",
            },
        ]
