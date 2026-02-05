"""
Organization and User Models

Multi-tenant support with Clerk integration.
"""

from datetime import datetime
from typing import Optional, List, TYPE_CHECKING

from sqlalchemy import String, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, generate_uuid

if TYPE_CHECKING:
    from .ticket import Ticket
    from .pipeline import Pipeline


class Organization(Base, TimestampMixin):
    """
    Organization model for multi-tenant support.

    Linked to Clerk organization via clerk_org_id.
    """
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    clerk_org_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)

    # Billing
    plan: Mapped[str] = mapped_column(String(50), default="trial")  # trial, starter, growth, enterprise
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    billing_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Settings
    settings_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    members: Mapped[List["OrgMember"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    credentials: Mapped[List["OrgCredential"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    tickets: Mapped[List["Ticket"]] = relationship(back_populates="organization")
    pipelines: Mapped[List["Pipeline"]] = relationship(back_populates="organization")

    def __repr__(self) -> str:
        return f"<Organization(id={self.id}, name={self.name}, plan={self.plan})>"


class User(Base, TimestampMixin):
    """
    User model for authentication.

    Linked to Clerk user via clerk_user_id.
    """
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    clerk_user_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Settings
    settings_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    memberships: Mapped[List["OrgMember"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email})>"


class OrgMember(Base, TimestampMixin):
    """
    Organization membership - links users to organizations with roles.
    """
    __tablename__ = "org_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    org_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="member")  # owner, admin, member

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")

    def __repr__(self) -> str:
        return f"<OrgMember(org_id={self.org_id}, user_id={self.user_id}, role={self.role})>"


class OrgCredential(Base, TimestampMixin):
    """
    Encrypted credentials for an organization.

    Credentials are encrypted using Vault or AWS Secrets Manager.
    """
    __tablename__ = "org_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    org_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    credential_type: Mapped[str] = mapped_column(String(50), nullable=False)  # jira, github, anthropic
    encrypted_data: Mapped[bytes] = mapped_column(nullable=False)  # Encrypted JSON
    key_id: Mapped[str] = mapped_column(String(255), nullable=False)  # Vault/Secrets Manager key reference
    created_by: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="credentials")

    def __repr__(self) -> str:
        return f"<OrgCredential(org_id={self.org_id}, type={self.credential_type})>"


class UsageEvent(Base):
    """
    Usage tracking for billing and analytics.
    """
    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    org_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)  # pipeline_run, step_completed, tokens_used
    resource_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)  # pipeline_id or step_id
    quantity: Mapped[int] = mapped_column(default=1)
    tokens_input: Mapped[Optional[int]] = mapped_column(nullable=True)
    tokens_output: Mapped[Optional[int]] = mapped_column(nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<UsageEvent(org_id={self.org_id}, type={self.event_type})>"


class UsageAggregate(Base):
    """
    Monthly usage aggregates for billing.
    """
    __tablename__ = "usage_aggregates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    org_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False)
    month: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM format
    pipelines_run: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    total_cost_usd: Mapped[float] = mapped_column(default=0.0)

    def __repr__(self) -> str:
        return f"<UsageAggregate(org_id={self.org_id}, month={self.month})>"


__all__ = [
    "Organization",
    "User",
    "OrgMember",
    "OrgCredential",
    "UsageEvent",
    "UsageAggregate",
]
