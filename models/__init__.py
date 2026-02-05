"""
BiAgent SQLAlchemy Models

Provides ORM models for all database entities with multi-tenant support.
"""

from .base import (
    Base,
    TimestampMixin,
    SoftDeleteMixin,
    generate_uuid,
    configure_database,
    get_async_session,
    create_all_tables,
    drop_all_tables,
)

from .organization import (
    Organization,
    User,
    OrgMember,
    OrgCredential,
    UsageEvent,
    UsageAggregate,
)

from .ticket import (
    Ticket,
    TicketLink,
    SyncStatus,
)

from .pipeline import (
    Pipeline,
    PipelineStep,
    StepOutput,
    ToolCall,
    StepFeedback,
    PullRequest,
    ReviewComment,
    WorktreeSession,
    WorktreeRepo,
    Session,
    SessionTab,
    Waitlist,
)


__all__ = [
    # Base
    "Base",
    "TimestampMixin",
    "SoftDeleteMixin",
    "generate_uuid",
    "configure_database",
    "get_async_session",
    "create_all_tables",
    "drop_all_tables",
    # Organization
    "Organization",
    "User",
    "OrgMember",
    "OrgCredential",
    "UsageEvent",
    "UsageAggregate",
    # Ticket
    "Ticket",
    "TicketLink",
    "SyncStatus",
    # Pipeline
    "Pipeline",
    "PipelineStep",
    "StepOutput",
    "ToolCall",
    "StepFeedback",
    "PullRequest",
    "ReviewComment",
    "WorktreeSession",
    "WorktreeRepo",
    "Session",
    "SessionTab",
    "Waitlist",
]
