"""
BiAgent Pydantic Schemas

Request/response validation schemas for the API.
"""

from .organization import (
    OrganizationBase,
    OrganizationCreate,
    OrganizationResponse,
    UserBase,
    UserCreate,
    UserResponse,
    OrgMemberResponse,
    OrgCredentialCreate,
    OrgCredentialResponse,
)

from .ticket import (
    TicketBase,
    TicketCreate,
    TicketResponse,
    TicketListResponse,
    TicketStatsResponse,
)

from .pipeline import (
    PipelineBase,
    PipelineCreate,
    PipelineResponse,
    PipelineStepResponse,
    StepOutputResponse,
    PipelineFeedbackRequest,
)

from .auth import (
    AuthUser,
    AuthOrganization,
    TokenPayload,
)

__all__ = [
    # Organization
    "OrganizationBase",
    "OrganizationCreate",
    "OrganizationResponse",
    "UserBase",
    "UserCreate",
    "UserResponse",
    "OrgMemberResponse",
    "OrgCredentialCreate",
    "OrgCredentialResponse",
    # Ticket
    "TicketBase",
    "TicketCreate",
    "TicketResponse",
    "TicketListResponse",
    "TicketStatsResponse",
    # Pipeline
    "PipelineBase",
    "PipelineCreate",
    "PipelineResponse",
    "PipelineStepResponse",
    "StepOutputResponse",
    "PipelineFeedbackRequest",
    # Auth
    "AuthUser",
    "AuthOrganization",
    "TokenPayload",
]
