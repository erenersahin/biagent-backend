"""
Base Agent Class

Provides common functionality for all specialized agents using Claude Agent SDK.
Supports subagents, sessions, custom tools, and comprehensive error handling.
"""

import asyncio
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

from claude_agent_sdk import (
    query,
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AgentDefinition,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
    SystemMessage,
    CLINotFoundError,
    CLIConnectionError,
    ProcessError,
    CLIJSONDecodeError,
    ClaudeSDKError,
)


# =============================================================================
# Cost Tracking Configuration
# =============================================================================

@dataclass
class CostTrackerConfig:
    """Configuration for cost tracking."""
    enabled: bool = True
    estimate_tokens: bool = True  # Estimate tokens if SDK doesn't provide them
    estimate_cost: bool = True    # Calculate cost from tokens if not provided

    # Pricing per 1M tokens (default: Claude Sonnet pricing)
    input_price_per_million: float = 3.0   # $3/1M input tokens
    output_price_per_million: float = 15.0  # $15/1M output tokens

    # Token estimation settings
    chars_per_token: int = 4  # Approximate chars per token for estimation


class CostTracker:
    """Tracks token usage and calculates costs.

    Can be enabled/disabled via configuration.
    """

    def __init__(self, config: Optional[CostTrackerConfig] = None):
        self.config = config or CostTrackerConfig()
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0

    def reset(self):
        """Reset all tracking values."""
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0

    def update_from_usage(self, usage: Any) -> None:
        """Extract usage data from SDK usage object."""
        if not self.config.enabled or not usage:
            return

        if hasattr(usage, 'input_tokens'):
            self.input_tokens += getattr(usage, 'input_tokens', 0) or 0
        if hasattr(usage, 'output_tokens'):
            self.output_tokens += getattr(usage, 'output_tokens', 0) or 0
        if hasattr(usage, 'total_tokens'):
            total = getattr(usage, 'total_tokens', 0) or 0
            if total > (self.input_tokens + self.output_tokens):
                self.input_tokens = total // 2
                self.output_tokens = total - self.input_tokens

    def update_from_stats(self, stats: Any) -> None:
        """Extract usage from session stats object."""
        if not self.config.enabled or not stats:
            return

        if hasattr(stats, 'input_tokens'):
            self.input_tokens = max(self.input_tokens, getattr(stats, 'input_tokens', 0) or 0)
        if hasattr(stats, 'output_tokens'):
            self.output_tokens = max(self.output_tokens, getattr(stats, 'output_tokens', 0) or 0)
        if hasattr(stats, 'cost_usd'):
            self.cost = max(self.cost, float(getattr(stats, 'cost_usd', 0) or 0))

    def update_cost(self, cost: Optional[float]) -> None:
        """Update cost from SDK-provided value."""
        if not self.config.enabled:
            return
        if cost:
            self.cost = float(cost)

    def estimate_from_content(self, input_text: str, output_text: str) -> None:
        """Estimate tokens from content length if not already tracked."""
        if not self.config.enabled or not self.config.estimate_tokens:
            return

        if self.total_tokens == 0 and (input_text or output_text):
            self.input_tokens = len(input_text) // self.config.chars_per_token
            self.output_tokens = len(output_text) // self.config.chars_per_token

    def calculate_cost(self) -> None:
        """Calculate cost from tokens if not already set."""
        if not self.config.enabled or not self.config.estimate_cost:
            return

        if self.cost == 0 and self.total_tokens > 0:
            input_cost = self.input_tokens * (self.config.input_price_per_million / 1_000_000)
            output_cost = self.output_tokens * (self.config.output_price_per_million / 1_000_000)
            self.cost = input_cost + output_cost

    @property
    def total_tokens(self) -> int:
        """Get total token count."""
        return self.input_tokens + self.output_tokens

    def get_result(self) -> dict:
        """Get tracking results as a dict."""
        if not self.config.enabled:
            return {"tokens_used": 0, "cost": 0.0}
        return {
            "tokens_used": self.total_tokens,
            "cost": self.cost,
        }


# Default cost tracker config (can be overridden)
DEFAULT_COST_CONFIG = CostTrackerConfig(enabled=True)


@dataclass
class AgentContext:
    """Context passed to agents for execution."""
    pipeline_id: str
    ticket_key: str
    ticket: dict
    codebase_path: str
    sandbox_branch: str
    step_1_output: Optional[dict] = None
    step_2_output: Optional[dict] = None
    step_3_output: Optional[dict] = None
    step_4_output: Optional[dict] = None
    step_5_output: Optional[dict] = None
    step_6_output: Optional[dict] = None
    step_7_output: Optional[dict] = None
    user_feedback: Optional[str] = None
    user_guidance: Optional[str] = None
    review_comments: Optional[list] = None
    pr: Optional[dict] = None


@dataclass
class AgentResult:
    """Result from agent execution."""
    content: str
    structured_output: Optional[dict] = None
    tokens_used: int = 0
    cost: float = 0.0
    files_created: list = field(default_factory=list)
    files_modified: list = field(default_factory=list)
    commit_sha: Optional[str] = None


class BaseAgent(ABC):
    """Base class for all specialized agents using Claude Agent SDK.

    Features:
    - Session management with ClaudeSDKClient
    - Subagent spawning via Task tool
    - Custom tools via MCP servers
    - Comprehensive error handling
    - Configurable cost tracking
    """

    # Map of tool names to SDK tool names
    TOOL_MAPPING = {
        "file_read": "Read",
        "file_write": "Write",
        "file_list": "Glob",
        "bash": "Bash",
        "grep": "Grep",
        "jira_cli": "Bash",
        "github_cli": "Bash",
    }

    def __init__(
        self,
        model: str,
        max_tokens: int,
        tools: list[str],
        cost_config: Optional[CostTrackerConfig] = None,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.tools = tools
        self.cost_config = cost_config or DEFAULT_COST_CONFIG

    def get_allowed_tools(self) -> list[str]:
        """Get list of SDK tool names based on configured tools."""
        sdk_tools = set()
        for tool in self.tools:
            if tool in self.TOOL_MAPPING:
                sdk_tools.add(self.TOOL_MAPPING[tool])
        # Always include basic tools
        sdk_tools.update(["Read", "Glob", "Grep"])
        return list(sdk_tools)

    def get_agent_options(self, context: AgentContext) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions for this agent."""
        return ClaudeAgentOptions(
            cwd=context.codebase_path,
            allowed_tools=self.get_allowed_tools(),
            permission_mode="acceptEdits",
            max_turns=50,
            system_prompt=self.system_prompt,
        )

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Get the system prompt for this agent."""
        pass

    @abstractmethod
    def build_user_prompt(self, context: AgentContext) -> str:
        """Build the user prompt from context."""
        pass

    async def execute(
        self,
        context: AgentContext,
        on_token: Optional[Callable[[str], Any]] = None,
        on_tool_call: Optional[Callable[[str, dict], Any]] = None,
    ) -> dict:
        """Execute the agent with streaming using Claude Agent SDK.

        Args:
            context: The agent context with ticket and pipeline info
            on_token: Callback for streaming text tokens
            on_tool_call: Callback for tool invocations

        Returns:
            dict with content, structured_output, tokens_used, and cost
        """
        user_prompt = self.build_user_prompt(context)
        options = self.get_agent_options(context)

        tracker = CostTracker(self.cost_config)
        full_response = ""

        try:
            async for message in query(prompt=user_prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text = block.text
                            full_response += text
                            if on_token:
                                result = on_token(text)
                                if asyncio.iscoroutine(result):
                                    await result

                        elif isinstance(block, ToolUseBlock):
                            if on_tool_call:
                                result = on_tool_call(block.name, block.input or {})
                                if asyncio.iscoroutine(result):
                                    await result

                elif isinstance(message, ResultMessage):
                    # Extract usage from ResultMessage
                    tracker.update_from_usage(getattr(message, 'usage', None))

                    # Extract cost if available
                    cost = getattr(message, 'cost_usd', None) or getattr(message, 'cost', None)
                    tracker.update_cost(cost)

                    # Check for session stats
                    stats = getattr(message, 'session_stats', None) or getattr(message, 'stats', None)
                    tracker.update_from_stats(stats)

        except CLINotFoundError as e:
            raise RuntimeError(
                "Claude Code CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
            ) from e
        except CLIConnectionError as e:
            raise RuntimeError(f"Failed to connect to Claude Code: {e}") from e
        except ProcessError as e:
            raise RuntimeError(f"Agent process failed (exit {e.exit_code}): {e.stderr}") from e
        except CLIJSONDecodeError as e:
            raise RuntimeError(f"Invalid response from Claude Code: {e}") from e
        except ClaudeSDKError as e:
            raise RuntimeError(f"Claude SDK error: {e}") from e

        # Estimate tokens if not provided by SDK
        tracker.estimate_from_content(user_prompt, full_response)

        # Calculate cost if not provided
        tracker.calculate_cost()

        return {
            "content": full_response,
            "structured_output": self.parse_output(full_response),
            **tracker.get_result(),
        }

    def parse_output(self, content: str) -> Optional[dict]:
        """Parse structured output from response. Override in subclasses."""
        return None


class SessionAgent(BaseAgent):
    """Agent that maintains session state across multiple queries.

    Use this for multi-turn conversations where context should be preserved.
    """

    def __init__(
        self,
        model: str,
        max_tokens: int,
        tools: list[str],
        cost_config: Optional[CostTrackerConfig] = None,
    ):
        super().__init__(model, max_tokens, tools, cost_config)
        self.client: Optional[ClaudeSDKClient] = None
        self.session_id: Optional[str] = None

    async def connect(self, context: AgentContext) -> str:
        """Initialize a session and return the session ID."""
        options = self.get_agent_options(context)
        self.client = ClaudeSDKClient(options=options)
        await self.client.connect()

        # Capture session ID from init message
        async for message in self.client.receive_messages():
            if isinstance(message, SystemMessage) and hasattr(message, 'subtype'):
                if message.subtype == 'init':
                    self.session_id = message.data.get('session_id')
                    break

        return self.session_id

    async def query(
        self,
        prompt: str,
        on_token: Optional[Callable[[str], Any]] = None,
        on_tool_call: Optional[Callable[[str, dict], Any]] = None,
    ) -> dict:
        """Execute a query within the session."""
        if not self.client:
            raise RuntimeError("Session not connected. Call connect() first.")

        await self.client.query(prompt)

        tracker = CostTracker(self.cost_config)
        full_response = ""

        async for message in self.client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text = block.text
                        full_response += text
                        if on_token:
                            result = on_token(text)
                            if asyncio.iscoroutine(result):
                                await result

                    elif isinstance(block, ToolUseBlock):
                        if on_tool_call:
                            result = on_tool_call(block.name, block.input or {})
                            if asyncio.iscoroutine(result):
                                await result

            elif isinstance(message, ResultMessage):
                # Extract usage from ResultMessage
                tracker.update_from_usage(getattr(message, 'usage', None))

                # Extract cost
                cost = getattr(message, 'cost_usd', None) or getattr(message, 'cost', None)
                tracker.update_cost(cost)

        # Estimate tokens if not provided
        tracker.estimate_from_content(prompt, full_response)

        # Calculate cost if not provided
        tracker.calculate_cost()

        return {
            "content": full_response,
            "structured_output": self.parse_output(full_response),
            **tracker.get_result(),
        }

    async def disconnect(self):
        """Disconnect the session."""
        if self.client:
            await self.client.disconnect()
            self.client = None

    async def execute(
        self,
        context: AgentContext,
        on_token: Optional[Callable[[str], Any]] = None,
        on_tool_call: Optional[Callable[[str, dict], Any]] = None,
    ) -> dict:
        """Execute with automatic session management."""
        try:
            await self.connect(context)
            user_prompt = self.build_user_prompt(context)
            return await self.query(user_prompt, on_token, on_tool_call)
        finally:
            await self.disconnect()


# Subagent definitions for pipeline steps
PIPELINE_SUBAGENTS = {
    "context_analyzer": AgentDefinition(
        description="Analyzes ticket context, requirements, and codebase structure",
        prompt="You are an expert at understanding software requirements and analyzing codebases.",
        tools=["Read", "Grep", "Glob"],
        model="sonnet"
    ),
    "risk_assessor": AgentDefinition(
        description="Identifies risks, blockers, and potential issues",
        prompt="You are a risk analyst specializing in software development risks.",
        tools=["Read", "Grep"],
        model="sonnet"
    ),
    "implementation_planner": AgentDefinition(
        description="Creates detailed implementation plans and task breakdowns",
        prompt="You are a software architect planning implementation strategies.",
        tools=["Read", "Grep", "Glob"],
        model="sonnet"
    ),
    "code_implementer": AgentDefinition(
        description="Writes and modifies code following best practices",
        prompt="You are an expert developer implementing features.",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        model="opus"
    ),
    "test_writer": AgentDefinition(
        description="Creates comprehensive tests for code changes",
        prompt="You are a QA engineer writing thorough tests.",
        tools=["Read", "Write", "Edit", "Bash", "Grep"],
        model="sonnet"
    ),
    "documentation_writer": AgentDefinition(
        description="Generates clear documentation and comments",
        prompt="You are a technical writer creating documentation.",
        tools=["Read", "Write", "Edit"],
        model="sonnet"
    ),
    "pr_creator": AgentDefinition(
        description="Creates pull requests with proper descriptions",
        prompt="You are creating well-documented pull requests.",
        tools=["Read", "Bash", "Grep"],
        model="sonnet"
    ),
    "code_reviewer": AgentDefinition(
        description="Reviews code for quality, security, and best practices",
        prompt="You are an expert code reviewer.",
        tools=["Read", "Grep", "Edit", "Bash"],
        model="sonnet"
    ),
}


def get_pipeline_agent_options(codebase_path: str) -> ClaudeAgentOptions:
    """Get options configured with all pipeline subagents."""
    return ClaudeAgentOptions(
        cwd=codebase_path,
        agents=PIPELINE_SUBAGENTS,
        allowed_tools=[
            "Task",  # Spawn subagents
            "Read", "Write", "Edit", "Bash", "Glob", "Grep",
        ],
        permission_mode="acceptEdits",
        max_turns=100,
    )
