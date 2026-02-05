"""Initial schema - all models

Revision ID: 001_initial
Revises: None
Create Date: 2025-01-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# Revision identifiers
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all initial tables."""

    # Organizations
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("clerk_org_id", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("plan", sa.String(50), default="trial"),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("billing_email", sa.String(255), nullable=True),
        sa.Column("settings_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Users
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("clerk_user_id", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("email", sa.String(255), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("settings_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Organization Members
    op.create_table(
        "org_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(50), default="member"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Organization Credentials
    op.create_table(
        "org_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("credential_type", sa.String(50), nullable=False),
        sa.Column("encrypted_data", sa.LargeBinary, nullable=False),
        sa.Column("key_id", sa.String(255), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Usage Events
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=True),
        sa.Column("quantity", sa.Integer, default=1),
        sa.Column("tokens_input", sa.Integer, nullable=True),
        sa.Column("tokens_output", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    # Usage Aggregates
    op.create_table(
        "usage_aggregates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("pipelines_run", sa.Integer, default=0),
        sa.Column("total_tokens", sa.Integer, default=0),
        sa.Column("total_cost_usd", sa.Float, default=0.0),
    )

    # Tickets
    op.create_table(
        "tickets_orm",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("key", sa.String(50), unique=True, nullable=False, index=True),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("priority", sa.String(50), nullable=True),
        sa.Column("issue_type", sa.String(50), nullable=True),
        sa.Column("assignee", sa.String(255), nullable=True),
        sa.Column("project_key", sa.String(50), nullable=True),
        sa.Column("epic_key", sa.String(50), nullable=True),
        sa.Column("epic_name", sa.String(255), nullable=True),
        sa.Column("raw_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column("jira_updated_at", sa.DateTime, nullable=True),
    )

    # Pipelines
    op.create_table(
        "pipelines_orm",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("ticket_id", sa.String(36), sa.ForeignKey("tickets_orm.id"), nullable=True),
        sa.Column("ticket_key", sa.String(50), nullable=False, index=True),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("current_step", sa.Integer, default=1),
        sa.Column("total_cost_usd", sa.Float, default=0.0),
        sa.Column("total_tokens", sa.Integer, default=0),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("worktree_path", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
    )

    # Waitlist
    op.create_table(
        "waitlist",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("company", sa.String(255), nullable=True),
        sa.Column("team_size", sa.String(50), nullable=True),
        sa.Column("use_case", sa.Text, nullable=True),
        sa.Column("referral_source", sa.String(100), nullable=True),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table("waitlist")
    op.drop_table("pipelines_orm")
    op.drop_table("tickets_orm")
    op.drop_table("usage_aggregates")
    op.drop_table("usage_events")
    op.drop_table("org_credentials")
    op.drop_table("org_members")
    op.drop_table("users")
    op.drop_table("organizations")
