"""
BiAgent Configuration

Environment-based configuration with Pydantic settings.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
from pathlib import Path


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    app_name: str = "BiAgent"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # Database
    db_path: str = str(Path(__file__).parent.parent / "data" / "biagent.db")

    # JIRA Configuration
    jira_base_url: Optional[str] = None
    jira_email: Optional[str] = None
    jira_api_token: Optional[str] = None
    jira_project_key: Optional[str] = None
    jira_sync_interval_minutes: int = 5

    # Developer Configuration
    developer_name: Optional[str] = None  # Display name for filtering (e.g., "Eren Ersahin")

    # GitHub Configuration
    github_token: Optional[str] = None
    github_webhook_secret: Optional[str] = None
    github_repo: Optional[str] = None  # owner/repo format

    # Anthropic Configuration
    anthropic_api_key: Optional[str] = None
    anthropic_auth_token: Optional[str] = None

    # Codebase Configuration
    codebase_path: str = Field(
        default="/workspace",
        description="Path to the target codebase for agents to work on"
    )
    sandbox_branch_prefix: str = "biagent/"

    # WebSocket Configuration
    ws_heartbeat_interval: int = 30
    token_buffer_size: int = 1000
    token_buffer_flush_interval_ms: int = 50

    # Agent Configuration
    max_retries_per_step: int = 3
    step_timeout_seconds: int = 600  # 10 minutes

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        env_prefix = "BIAGENT_"


# Global settings instance
settings = Settings()


# Step configurations
STEP_CONFIGS = {
    1: {
        "name": "Context & Requirements",
        "agent_type": "context",
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4096,
        "tools": ["jira_cli", "file_read", "notion_mcp"],
        "system_prompt": "context_agent",
        "output_type": "context",
    },
    2: {
        "name": "Risk & Blocker Analysis",
        "agent_type": "risk",
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2048,
        "tools": ["jira_cli", "file_read"],
        "system_prompt": "risk_agent",
        "output_type": "risks",
    },
    3: {
        "name": "Implementation Planning",
        "agent_type": "planning",
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4096,
        "tools": ["file_read", "file_list"],
        "system_prompt": "planning_agent",
        "output_type": "plan",
    },
    4: {
        "name": "Code Implementation",
        "agent_type": "coding",
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 8192,
        "tools": ["file_read", "file_write", "bash", "jira_cli"],
        "system_prompt": "coding_agent",
        "output_type": "code",
    },
    5: {
        "name": "Test Writing & Execution",
        "agent_type": "testing",
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4096,
        "tools": ["file_read", "file_write", "bash"],
        "system_prompt": "testing_agent",
        "output_type": "tests",
    },
    6: {
        "name": "Documentation Updates",
        "agent_type": "docs",
        "model": "claude-haiku-3-5-20241022",
        "max_tokens": 2048,
        "tools": ["file_read", "file_write"],
        "system_prompt": "docs_agent",
        "output_type": "docs",
    },
    7: {
        "name": "PR Creation & Description",
        "agent_type": "pr",
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2048,
        "tools": ["bash", "github_cli"],
        "system_prompt": "pr_agent",
        "output_type": "pr",
    },
    8: {
        "name": "Code Review Response",
        "agent_type": "review",
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4096,
        "tools": ["file_read", "file_write", "bash", "github_cli"],
        "system_prompt": "review_agent",
        "output_type": "review",
    },
}


def get_step_config(step_number: int) -> dict:
    """Get configuration for a specific step."""
    return STEP_CONFIGS.get(step_number, {})


def get_step_name(step_number: int) -> str:
    """Get the name of a specific step."""
    config = STEP_CONFIGS.get(step_number)
    return config["name"] if config else f"Step {step_number}"
