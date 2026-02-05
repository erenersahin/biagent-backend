"""
Ticket Model

JIRA ticket cache with multi-tenant support.
"""

from datetime import datetime
from typing import Optional, List, TYPE_CHECKING

from sqlalchemy import String, Text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from .organization import Organization
    from .pipeline import Pipeline


class Ticket(Base, TimestampMixin):
    """
    JIRA ticket cache.

    Stores synced tickets for offline access and agent processing.
    """
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)  # PROJ-123
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    priority: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    assignee: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    project_key: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    issue_type: Mapped[str] = mapped_column(String(50), default="feature")
    epic_key: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    epic_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    jira_created_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    jira_updated_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    local_updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Full JIRA response

    # Relationships
    organization: Mapped[Optional["Organization"]] = relationship(back_populates="tickets")
    pipelines: Mapped[List["Pipeline"]] = relationship(back_populates="ticket")
    links_from: Mapped[List["TicketLink"]] = relationship(
        back_populates="source_ticket",
        foreign_keys="TicketLink.source_key",
        primaryjoin="Ticket.key == TicketLink.source_key"
    )
    links_to: Mapped[List["TicketLink"]] = relationship(
        back_populates="target_ticket",
        foreign_keys="TicketLink.target_key",
        primaryjoin="Ticket.key == TicketLink.target_key"
    )

    def __repr__(self) -> str:
        return f"<Ticket(key={self.key}, status={self.status})>"


class TicketLink(Base):
    """
    Links between tickets (blocks, is blocked by, relates to, etc.)
    """
    __tablename__ = "ticket_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    source_key: Mapped[str] = mapped_column(String(50), ForeignKey("tickets.key"), nullable=False, index=True)
    target_key: Mapped[str] = mapped_column(String(50), nullable=False)
    link_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # blocks, is blocked by, relates to

    # Relationships
    source_ticket: Mapped["Ticket"] = relationship(
        back_populates="links_from",
        foreign_keys=[source_key]
    )
    target_ticket: Mapped[Optional["Ticket"]] = relationship(
        back_populates="links_to",
        foreign_keys=[target_key],
        primaryjoin="TicketLink.target_key == Ticket.key"
    )

    def __repr__(self) -> str:
        return f"<TicketLink(source={self.source_key}, target={self.target_key}, type={self.link_type})>"


class SyncStatus(Base):
    """
    JIRA sync status tracking.
    """
    __tablename__ = "sync_status"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    org_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=True)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    sync_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # initial, auto, webhook, manual
    tickets_updated: Mapped[Optional[int]] = mapped_column(nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<SyncStatus(type={self.sync_type}, updated={self.tickets_updated})>"


__all__ = [
    "Ticket",
    "TicketLink",
    "SyncStatus",
]
