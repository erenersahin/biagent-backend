"""
Authentication Schemas

Pydantic models for auth-related data.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class AuthOrganization(BaseModel):
    """Organization context from Clerk token."""
    id: str
    clerk_org_id: str
    name: str
    slug: str
    role: str  # owner, admin, member


class AuthUser(BaseModel):
    """Authenticated user context from Clerk."""
    id: str
    clerk_user_id: str
    email: str
    name: Optional[str] = None
    organizations: List[AuthOrganization] = []
    current_org: Optional[AuthOrganization] = None

    @property
    def is_authenticated(self) -> bool:
        return bool(self.clerk_user_id)

    @property
    def has_org_context(self) -> bool:
        return self.current_org is not None


class TokenPayload(BaseModel):
    """JWT token payload from Clerk."""
    sub: str  # Clerk user ID
    iss: str  # Issuer
    aud: Optional[str] = None
    exp: int  # Expiration timestamp
    iat: int  # Issued at timestamp
    nbf: Optional[int] = None  # Not before timestamp
    azp: Optional[str] = None  # Authorized party
    sid: Optional[str] = None  # Session ID
    org_id: Optional[str] = None  # Organization ID (if in org context)
    org_role: Optional[str] = None  # Role in organization
    org_slug: Optional[str] = None  # Organization slug
    org_permissions: Optional[List[str]] = None  # Organization permissions


class SessionInfo(BaseModel):
    """UI session information."""
    id: str
    user_id: Optional[str] = None
    created_at: datetime
    last_active_at: datetime
    active_tab: Optional[str] = None


class AuthStatusResponse(BaseModel):
    """Response for auth status endpoint."""
    authenticated: bool
    user: Optional[AuthUser] = None
    session: Optional[SessionInfo] = None
    auth_enabled: bool = True
    tier: str = "consumer"  # consumer, starter, growth, enterprise
