"""
Ticket Schemas

Pydantic models for ticket API operations.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class TicketBase(BaseModel):
    """Base ticket schema."""
    key: str = Field(..., min_length=1, max_length=50)
    summary: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    status: str = Field(..., min_length=1)
    priority: Optional[str] = None
    assignee: Optional[str] = None


class TicketCreate(TicketBase):
    """Schema for creating/syncing a ticket."""
    id: str
    project_key: Optional[str] = None
    issue_type: str = "feature"
    epic_key: Optional[str] = None
    epic_name: Optional[str] = None
    jira_created_at: Optional[datetime] = None
    jira_updated_at: Optional[datetime] = None
    raw_json: Optional[str] = None


class TicketResponse(TicketBase):
    """Schema for ticket API responses."""
    id: str
    project_key: Optional[str] = None
    issue_type: str
    epic_key: Optional[str] = None
    epic_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    local_updated_at: Optional[datetime] = None

    # Pipeline status (joined from pipelines table)
    pipeline_status: Optional[str] = None
    pipeline_id: Optional[str] = None

    class Config:
        from_attributes = True


class TicketLinkResponse(BaseModel):
    """Schema for ticket link responses."""
    id: str
    source_key: str
    target_key: str
    link_type: Optional[str] = None

    class Config:
        from_attributes = True


class TicketListResponse(BaseModel):
    """Schema for paginated ticket list responses."""
    tickets: List[TicketResponse]
    total: int
    page: int = 1
    page_size: int = 50


class TicketStatsResponse(BaseModel):
    """Schema for ticket statistics."""
    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    by_assignee: dict[str, int]
    assigned_to_me: int = 0
    in_progress: int = 0
    completed_today: int = 0


class SyncStatusResponse(BaseModel):
    """Schema for sync status responses."""
    id: str
    last_sync_at: Optional[datetime] = None
    sync_type: Optional[str] = None
    tickets_updated: Optional[int] = None
    error: Optional[str] = None

    class Config:
        from_attributes = True
