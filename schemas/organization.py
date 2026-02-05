"""
Organization and User Schemas

Pydantic models for organization and user API operations.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field


class OrganizationBase(BaseModel):
    """Base organization schema."""
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")


class OrganizationCreate(OrganizationBase):
    """Schema for creating an organization."""
    clerk_org_id: str = Field(..., min_length=1)
    plan: str = Field(default="trial")
    billing_email: Optional[EmailStr] = None


class OrganizationResponse(OrganizationBase):
    """Schema for organization API responses."""
    id: str
    clerk_org_id: str
    plan: str
    stripe_customer_id: Optional[str] = None
    billing_email: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    member_count: Optional[int] = None

    class Config:
        from_attributes = True


class UserBase(BaseModel):
    """Base user schema."""
    email: EmailStr
    name: Optional[str] = None


class UserCreate(UserBase):
    """Schema for creating a user."""
    clerk_user_id: str = Field(..., min_length=1)


class UserResponse(UserBase):
    """Schema for user API responses."""
    id: str
    clerk_user_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class OrgMemberResponse(BaseModel):
    """Schema for organization member responses."""
    id: str
    org_id: str
    user_id: str
    role: str
    user: Optional[UserResponse] = None
    created_at: datetime

    class Config:
        from_attributes = True


class OrgCredentialCreate(BaseModel):
    """Schema for creating organization credentials."""
    credential_type: str = Field(..., pattern=r"^(jira|github|anthropic)$")
    data: dict = Field(..., description="Credential data to be encrypted")


class OrgCredentialResponse(BaseModel):
    """Schema for organization credential responses (without sensitive data)."""
    id: str
    org_id: str
    credential_type: str
    created_at: datetime
    is_configured: bool = True

    class Config:
        from_attributes = True


class UsageEventCreate(BaseModel):
    """Schema for creating usage events."""
    event_type: str
    resource_id: Optional[str] = None
    quantity: int = 1
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    cost_usd: Optional[float] = None


class UsageAggregateResponse(BaseModel):
    """Schema for usage aggregate responses."""
    month: str
    pipelines_run: int
    total_tokens: int
    total_cost_usd: float

    class Config:
        from_attributes = True
