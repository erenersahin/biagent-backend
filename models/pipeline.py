"""
Pipeline Models

Pipeline execution tracking with multi-tenant support.
"""

from datetime import datetime
from typing import Optional, List, TYPE_CHECKING

from sqlalchemy import String, Text, Integer, Float, Boolean, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from .organization import Organization
    from .ticket import Ticket


class Pipeline(Base, TimestampMixin):
    """
    Pipeline execution record.

    Tracks the 8-step agent pipeline for a ticket.
    """
    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    org_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=True, index=True)
    ticket_key: Mapped[str] = mapped_column(String(50), ForeignKey("tickets.key"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    # Status: pending, running, paused, completed, failed, waiting_for_review, suspended
    current_step: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    paused_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    organization: Mapped[Optional["Organization"]] = relationship(back_populates="pipelines")
    ticket: Mapped["Ticket"] = relationship(back_populates="pipelines")
    steps: Mapped[List["PipelineStep"]] = relationship(back_populates="pipeline", cascade="all, delete-orphan")
    pull_requests: Mapped[List["PullRequest"]] = relationship(back_populates="pipeline", cascade="all, delete-orphan")
    worktree_session: Mapped[Optional["WorktreeSession"]] = relationship(back_populates="pipeline", uselist=False)

    def __repr__(self) -> str:
        return f"<Pipeline(id={self.id}, ticket={self.ticket_key}, status={self.status})>"


class PipelineStep(Base):
    """
    Individual step within a pipeline.
    """
    __tablename__ = "pipeline_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    pipeline_id: Mapped[str] = mapped_column(String(36), ForeignKey("pipelines.id"), nullable=False, index=True)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    # Status: pending, running, paused, completed, failed, skipped, waiting
    started_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_feedback_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    waiting_for: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # github_webhook, etc.
    iteration_count: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    pipeline: Mapped["Pipeline"] = relationship(back_populates="steps")
    outputs: Mapped[List["StepOutput"]] = relationship(back_populates="step", cascade="all, delete-orphan")
    tool_calls: Mapped[List["ToolCall"]] = relationship(back_populates="step", cascade="all, delete-orphan")
    feedback: Mapped[List["StepFeedback"]] = relationship(back_populates="step", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<PipelineStep(pipeline={self.pipeline_id}, step={self.step_number}, status={self.status})>"


class StepOutput(Base):
    """
    Output/artifact from a pipeline step.
    """
    __tablename__ = "step_outputs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    step_id: Mapped[str] = mapped_column(String(36), ForeignKey("pipeline_steps.id"), nullable=False, index=True)
    output_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Types: context, risks, plan, code, tests, docs, pr, review
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Structured output
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Relationships
    step: Mapped["PipelineStep"] = relationship(back_populates="outputs")

    def __repr__(self) -> str:
        return f"<StepOutput(step={self.step_id}, type={self.output_type})>"


class ToolCall(Base):
    """
    Tool call logging for a step.
    """
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    step_id: Mapped[str] = mapped_column(String(36), ForeignKey("pipeline_steps.id"), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Relationships
    step: Mapped["PipelineStep"] = relationship(back_populates="tool_calls")

    def __repr__(self) -> str:
        return f"<ToolCall(step={self.step_id}, tool={self.tool_name})>"


class StepFeedback(Base):
    """
    User feedback for a step.
    """
    __tablename__ = "step_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    step_id: Mapped[str] = mapped_column(String(36), ForeignKey("pipeline_steps.id"), nullable=False, index=True)
    feedback_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    applied: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    step: Mapped["PipelineStep"] = relationship(back_populates="feedback")

    def __repr__(self) -> str:
        return f"<StepFeedback(step={self.step_id})>"


class PullRequest(Base, TimestampMixin):
    """
    Pull request tracking.
    """
    __tablename__ = "pull_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    pipeline_id: Mapped[str] = mapped_column(String(36), ForeignKey("pipelines.id"), nullable=False, index=True)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pr_url: Mapped[str] = mapped_column(String(500), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="open")  # open, approved, merged, closed
    approval_count: Mapped[int] = mapped_column(Integer, default=0)
    approved_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    merged_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # Relationships
    pipeline: Mapped["Pipeline"] = relationship(back_populates="pull_requests")
    comments: Mapped[List["ReviewComment"]] = relationship(back_populates="pull_request", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<PullRequest(pipeline={self.pipeline_id}, pr={self.pr_number}, status={self.status})>"


class ReviewComment(Base):
    """
    Review comment from GitHub.
    """
    __tablename__ = "review_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    pr_id: Mapped[str] = mapped_column(String(36), ForeignKey("pull_requests.id"), nullable=False, index=True)
    github_comment_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    comment_body: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    line_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reviewer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    review_state: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # comment, approve, changes_requested
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # Relationships
    pull_request: Mapped["PullRequest"] = relationship(back_populates="comments")

    def __repr__(self) -> str:
        return f"<ReviewComment(pr={self.pr_id}, reviewer={self.reviewer})>"


class WorktreeSession(Base, TimestampMixin):
    """
    Git worktree session for pipeline isolation.
    """
    __tablename__ = "worktree_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    pipeline_id: Mapped[str] = mapped_column(String(36), ForeignKey("pipelines.id"), nullable=False, index=True)
    ticket_key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    # Status: pending, creating, ready, needs_user_input, failed, cleaned
    base_path: Mapped[str] = mapped_column(String(500), nullable=False)
    ready_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    cleaned_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_input_request: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    user_input_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON

    # Relationships
    pipeline: Mapped["Pipeline"] = relationship(back_populates="worktree_session")
    repos: Mapped[List["WorktreeRepo"]] = relationship(back_populates="session", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<WorktreeSession(pipeline={self.pipeline_id}, status={self.status})>"


class WorktreeRepo(Base, TimestampMixin):
    """
    Individual repo worktree within a session.
    """
    __tablename__ = "worktree_repos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("worktree_sessions.id"), nullable=False, index=True)
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False)
    repo_path: Mapped[str] = mapped_column(String(500), nullable=False)
    worktree_path: Mapped[str] = mapped_column(String(500), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    # Status: pending, creating, setup, ready, failed
    setup_commands: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    setup_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pr_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
    pr_merged: Mapped[bool] = mapped_column(Boolean, default=False)
    ready_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # Relationships
    session: Mapped["WorktreeSession"] = relationship(back_populates="repos")

    def __repr__(self) -> str:
        return f"<WorktreeRepo(session={self.session_id}, repo={self.repo_name})>"


class Session(Base):
    """
    UI session tracking.
    """
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_active_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    active_tab: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ui_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON

    # Relationships
    tabs: Mapped[List["SessionTab"]] = relationship(back_populates="session", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Session(id={self.id})>"


class SessionTab(Base):
    """
    Open tabs within a session.
    """
    __tablename__ = "session_tabs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False, index=True)
    ticket_key: Mapped[str] = mapped_column(String(50), nullable=False)
    pipeline_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("pipelines.id"), nullable=True)
    tab_order: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_viewed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # Relationships
    session: Mapped["Session"] = relationship(back_populates="tabs")

    def __repr__(self) -> str:
        return f"<SessionTab(session={self.session_id}, ticket={self.ticket_key})>"


class Waitlist(Base):
    """
    Waitlist signups.
    """
    __tablename__ = "waitlist"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # developer, lead, manager, founder, other
    use_cases: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    def __repr__(self) -> str:
        return f"<Waitlist(email={self.email})>"


__all__ = [
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
