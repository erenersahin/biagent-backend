"""
Pipeline Schemas

Pydantic models for pipeline API operations.
"""

from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field


class StepOutputResponse(BaseModel):
    """Schema for step output responses."""
    id: str
    step_id: str
    output_type: str
    content: Optional[str] = None
    content_json: Optional[Any] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ToolCallResponse(BaseModel):
    """Schema for tool call responses."""
    id: str
    step_id: str
    tool_name: str
    arguments: Optional[str] = None
    result: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class StepFeedbackResponse(BaseModel):
    """Schema for step feedback responses."""
    id: str
    step_id: str
    feedback_text: str
    created_at: datetime
    applied: bool

    class Config:
        from_attributes = True


class PipelineStepResponse(BaseModel):
    """Schema for pipeline step responses."""
    id: str
    pipeline_id: str
    step_number: int
    step_name: str
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tokens_used: int = 0
    cost: float = 0.0
    error_message: Optional[str] = None
    retry_count: int = 0
    waiting_for: Optional[str] = None
    iteration_count: int = 0

    # Related data
    outputs: Optional[List[StepOutputResponse]] = None
    tool_calls: Optional[List[ToolCallResponse]] = None
    feedback: Optional[List[StepFeedbackResponse]] = None

    class Config:
        from_attributes = True


class PipelineBase(BaseModel):
    """Base pipeline schema."""
    ticket_key: str = Field(..., min_length=1)


class PipelineCreate(PipelineBase):
    """Schema for creating a pipeline."""
    pass


class PipelineResponse(PipelineBase):
    """Schema for pipeline API responses."""
    id: str
    org_id: Optional[str] = None
    status: str
    current_step: int
    started_at: Optional[datetime] = None
    paused_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_tokens: int = 0
    total_cost: float = 0.0
    pause_requested: bool = False
    created_at: datetime
    updated_at: datetime

    # Related data
    steps: Optional[List[PipelineStepResponse]] = None

    class Config:
        from_attributes = True


class PipelineFeedbackRequest(BaseModel):
    """Schema for providing feedback to a pipeline step."""
    step_number: int = Field(..., ge=1, le=8)
    feedback: str = Field(..., min_length=1)
    restart_from_step: bool = False


class PipelineStartRequest(BaseModel):
    """Schema for starting a pipeline."""
    max_steps: Optional[int] = Field(default=None, ge=1, le=8)


class PipelinePauseRequest(BaseModel):
    """Schema for pausing a pipeline."""
    reason: Optional[str] = None


class PullRequestResponse(BaseModel):
    """Schema for pull request responses."""
    id: str
    pipeline_id: str
    pr_number: int
    pr_url: str
    branch: str
    status: str
    approval_count: int = 0
    created_at: datetime
    approved_at: Optional[datetime] = None
    merged_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ReviewCommentResponse(BaseModel):
    """Schema for review comment responses."""
    id: str
    pr_id: str
    github_comment_id: Optional[str] = None
    comment_body: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    reviewer: Optional[str] = None
    review_state: Optional[str] = None
    processed: bool = False
    agent_response: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class WorktreeSessionResponse(BaseModel):
    """Schema for worktree session responses."""
    id: str
    pipeline_id: str
    ticket_key: str
    status: str
    base_path: str
    created_at: datetime
    ready_at: Optional[datetime] = None
    error_message: Optional[str] = None
    user_input_request: Optional[Any] = None

    class Config:
        from_attributes = True


class UserInputRequest(BaseModel):
    """Schema for providing user input to a worktree session."""
    response: dict = Field(..., description="User's response to the input request")
