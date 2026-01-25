"""
Base Agent Class

Provides common functionality for all specialized agents using Claude Agent SDK.
Supports subagents, sessions, custom tools, and comprehensive error handling.
"""

import asyncio
import json
import re
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
    step_id: Optional[str] = None  # Current step ID for clarification requests
    step_1_output: Optional[dict] = None
    step_2_output: Optional[dict] = None
    step_3_output: Optional[dict] = None
    step_4_output: Optional[dict] = None
    step_5_output: Optional[dict] = None
    step_6_output: Optional[dict] = None
    step_7_output: Optional[dict] = None
    user_feedback: Optional[str] = None
    user_guidance: Optional[str] = None
    clarification_answer: Optional[str] = None  # Answer to a previous clarification
    review_comments: Optional[list] = None
    pr: Optional[dict] = None
    # Worktree isolation fields
    is_worktree: bool = False  # Whether running in an isolated worktree
    worktree_paths: Optional[dict] = None  # Map of repo_name -> worktree_path


@dataclass
class ClarificationRequest:
    """A request for user clarification from an agent."""
    question: str
    options: list[str]
    context: str = ""


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
    clarification_request: Optional[ClarificationRequest] = None  # If agent needs user input


# Clarification instruction to append to agent prompts
CLARIFICATION_INSTRUCTION = """
## REQUESTING CLARIFICATION

If you encounter any of these situations, you MUST request clarification before proceeding:
- Ambiguous requirements that could be interpreted multiple ways
- Missing critical information needed to proceed
- Multiple valid implementation approaches where user preference matters
- Potential breaking changes that need explicit approval
- Security or data handling decisions that require user input

To request clarification, output a JSON block with this EXACT format:

```clarification
{
  "question": "Your clear, specific question",
  "options": ["Option 1", "Option 2", "Option 3"],
  "context": "Why you're asking and what impact each option has"
}
```

IMPORTANT:
- Use 2-4 options that cover the reasonable choices
- The question must be specific and actionable
- After outputting the clarification request, STOP and wait - do not continue with implementation
"""


def parse_clarification_request(content: str) -> Optional[ClarificationRequest]:
    """Parse a clarification request from agent output.

    Looks for a ```clarification code block with JSON content.
    """
    # Look for clarification code block
    pattern = r'```clarification\s*([\s\S]*?)\s*```'
    match = re.search(pattern, content)

    if not match:
        return None

    try:
        data = json.loads(match.group(1))
        question = data.get("question", "")
        options = data.get("options", [])
        context = data.get("context", "")

        if question and options and len(options) >= 2:
            return ClarificationRequest(
                question=question,
                options=options[:4],  # Max 4 options
                context=context
            )
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    return None


class BaseAgent(ABC):
    """Base class for all specialized agents using Claude Agent SDK.

    Features:
    - Session management with ClaudeSDKClient
    - Subagent spawning via Task tool
    - Custom tools via MCP servers
    - Comprehensive error handling
    - Configurable cost tracking
    - Clarification requests for ambiguous situations
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

    # Whether this agent type can request clarifications
    CAN_REQUEST_CLARIFICATION = True

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
        # Build system prompt with clarification instruction if enabled
        system_prompt = self.system_prompt
        if self.CAN_REQUEST_CLARIFICATION:
            system_prompt += "\n\n" + CLARIFICATION_INSTRUCTION

        return ClaudeAgentOptions(
            cwd=context.codebase_path,
            allowed_tools=self.get_allowed_tools(),
            permission_mode="acceptEdits",
            max_turns=50,
            system_prompt=system_prompt,
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
        on_tool_call: Optional[Callable[[str, dict, str], Any]] = None,
    ) -> dict:
        """Execute the agent with streaming using Claude Agent SDK.

        Args:
            context: The agent context with ticket and pipeline info
            on_token: Callback for streaming text tokens
            on_tool_call: Callback for tool invocations (tool_name, args, tool_use_id)

        Returns:
            dict with content, structured_output, tokens_used, and cost
        """
        user_prompt = self.build_user_prompt(context)

        # Prepend clarification answer if resuming from a clarification
        if context.clarification_answer:
            user_prompt = (
                f"## CLARIFICATION RESPONSE\n\n"
                f"You previously asked for clarification. The user's answer is:\n"
                f"**{context.clarification_answer}**\n\n"
                f"Please continue with your task using this information.\n\n"
                f"---\n\n{user_prompt}"
            )

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
                                result = on_tool_call(block.name, block.input or {}, block.id)
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

        # Check for clarification request in response
        clarification = None
        if self.CAN_REQUEST_CLARIFICATION:
            clarification = parse_clarification_request(full_response)

        result = {
            "content": full_response,
            "structured_output": self.parse_output(full_response),
            **tracker.get_result(),
        }

        if clarification:
            result["clarification_request"] = {
                "question": clarification.question,
                "options": clarification.options,
                "context": clarification.context,
            }

        return result

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


# =============================================================================
# Pipeline Step Subagent Definitions
# =============================================================================
# These subagents are spawned via the Task tool by the main pipeline orchestrator.
# Each maps to a pipeline step and contains the specialized prompt for that step.

PIPELINE_SUBAGENTS = {
    # Step 1: Context & Requirements
    "context_agent": AgentDefinition(
        description="Gathers context and requirements for JIRA tickets by analyzing the ticket, searching the codebase, and identifying relevant files and patterns",
        prompt="""You are the Context & Requirements Agent for BiAgent, an AI-powered development system.

Your role is to gather all necessary context for implementing a JIRA ticket. You will:

1. Analyze the ticket details (summary, description, acceptance criteria)
2. Find related tickets and dependencies
3. Search the codebase for relevant files and patterns
4. Identify key requirements and constraints

OUTPUT FORMAT:
Your output should be a comprehensive context summary that includes:
- Ticket summary and key requirements
- Related tickets and their status
- Relevant codebase files identified
- Technical constraints discovered
- Any questions or ambiguities found

Be thorough but concise. Focus on information that will help the subsequent agents.""",
        tools=["Read", "Grep", "Glob", "Bash"],
        model="sonnet"
    ),

    # Step 2: Risk & Blocker Analysis
    "risk_agent": AgentDefinition(
        description="Identifies technical risks, blockers, and potential issues that could impact implementation",
        prompt="""You are the Risk & Blocker Analysis Agent for BiAgent.

Your role is to identify potential risks and blockers BEFORE implementation begins. You will:

1. Analyze the context gathered in Step 1
2. Identify technical risks (complexity, dependencies, breaking changes)
3. Find potential blockers (missing info, external dependencies, permissions)
4. Assess security implications
5. Estimate effort and flag time-sensitive concerns

OUTPUT FORMAT - Return a JSON block with risk cards:
```json
{
  "risk_cards": [
    {
      "severity": "high|medium|low",
      "category": "technical|dependency|security|scope|timeline",
      "title": "Brief risk title",
      "description": "Detailed description of the risk",
      "mitigation": "Suggested mitigation strategy",
      "is_blocker": true|false
    }
  ],
  "overall_risk_level": "high|medium|low",
  "recommendation": "proceed|pause|clarify"
}
```

Be thorough in identifying risks. It's better to flag a potential issue early than discover it during implementation.""",
        tools=["Read", "Grep", "Glob"],
        model="sonnet"
    ),

    # Step 3: Implementation Planning
    "planning_agent": AgentDefinition(
        description="Creates detailed implementation plans with task breakdowns and file change specifications",
        prompt="""You are the Implementation Planning Agent for BiAgent.

Your role is to create a detailed, actionable implementation plan based on the context and risk analysis. You will:

1. Break down the implementation into discrete tasks
2. Identify files that need to be created, modified, or deleted
3. Define the order of operations
4. Specify test requirements
5. Account for identified risks in the plan

OUTPUT FORMAT:
Provide a structured plan including:
- High-level approach summary
- Task list with clear acceptance criteria for each
- File changes matrix (which files to touch)
- Test plan overview
- Rollback considerations

Make the plan specific enough that another agent can execute it without ambiguity.""",
        tools=["Read", "Grep", "Glob"],
        model="sonnet"
    ),

    # Step 4: Code Implementation
    "coding_agent": AgentDefinition(
        description="Implements code changes following the plan, maintaining code quality and consistency with existing patterns",
        prompt="""You are the Code Implementation Agent for BiAgent.

Your role is to implement the planned changes. You will:

1. Follow the implementation plan from Step 3
2. Write clean, maintainable code
3. Follow existing code patterns and conventions
4. Handle edge cases appropriately
5. Add appropriate comments where needed

GUIDELINES:
- Match the existing code style exactly
- Don't over-engineer - implement exactly what's needed
- Create appropriate error handling
- Avoid breaking existing functionality
- Keep commits atomic and well-described

After implementation, provide a summary of:
- Files created/modified
- Key implementation decisions
- Any deviations from the plan and why""",
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        model="sonnet"
    ),

    # Step 5: Test Writing & Execution
    "testing_agent": AgentDefinition(
        description="Creates comprehensive tests for code changes and runs existing test suites",
        prompt="""You are the Testing Agent for BiAgent.

Your role is to ensure code quality through comprehensive testing. You will:

1. Write unit tests for new/modified functions
2. Write integration tests where appropriate
3. Run the existing test suite
4. Verify the implementation meets requirements
5. Check for edge cases and error conditions

OUTPUT FORMAT:
- List of tests created
- Test coverage summary
- Test execution results
- Any failing tests and their causes
- Recommendations for additional testing

Ensure tests are maintainable and follow the project's testing patterns.""",
        tools=["Read", "Write", "Edit", "Bash", "Grep"],
        model="sonnet"
    ),

    # Step 6: Documentation Updates
    "docs_agent": AgentDefinition(
        description="Updates documentation including READMEs, API docs, and inline comments",
        prompt="""You are the Documentation Agent for BiAgent.

Your role is to ensure all changes are properly documented. You will:

1. Update README files if needed
2. Add/update API documentation
3. Update changelog entries
4. Ensure code comments are clear and helpful
5. Update any related documentation

GUIDELINES:
- Keep documentation concise and actionable
- Use clear examples where helpful
- Follow existing documentation style
- Don't document the obvious

Output a summary of documentation updates made.""",
        tools=["Read", "Write", "Edit"],
        model="haiku"
    ),

    # Step 7: PR Creation
    "pr_agent": AgentDefinition(
        description="Creates pull requests with comprehensive descriptions, test instructions, and proper labels",
        prompt="""You are the PR Creation Agent for BiAgent.

Your role is to create a well-documented pull request. You will:

1. Stage and commit all changes with clear commit messages
2. Push to the appropriate branch
3. Create a PR with comprehensive description
4. Add appropriate labels and reviewers
5. Link to the original ticket

PR DESCRIPTION FORMAT:
## Summary
[What this PR does in 1-2 sentences]

## Changes
- [List of key changes]

## Testing
- [How to test these changes]

## Related
- Ticket: [TICKET-KEY]

Ensure the PR is ready for review.""",
        tools=["Read", "Bash", "Grep"],
        model="sonnet"
    ),

    # Step 8: Code Review Response
    "review_agent": AgentDefinition(
        description="Addresses PR review comments by making requested changes and responding to feedback",
        prompt="""You are the Code Review Response Agent for BiAgent.

Your role is to address PR review feedback. You will:

1. Read and understand each review comment
2. Make requested changes where appropriate
3. Respond to questions and concerns
4. Push updated commits
5. Mark conversations as resolved where appropriate

GUIDELINES:
- Address each comment thoughtfully
- If you disagree with feedback, explain your reasoning
- Keep commits focused on specific feedback items
- Update tests if code changes affect them

Provide a summary of:
- Comments addressed
- Changes made
- Any unresolved discussions""",
        tools=["Read", "Write", "Edit", "Bash", "Grep"],
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
