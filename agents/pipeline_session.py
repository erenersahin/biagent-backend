"""
Pipeline Session - Persistent Claude Session for Pipeline Execution

Manages a single ClaudeSDKClient instance across all pipeline steps,
enabling context persistence where Claude remembers everything from
previous steps without requiring explicit context replay.
"""

import asyncio
from typing import Optional, Callable, Any
from dataclasses import dataclass, field

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
    CLINotFoundError,
    CLIConnectionError,
    ProcessError,
    CLIJSONDecodeError,
    ClaudeSDKError,
)

from .base import (
    BaseAgent,
    AgentContext,
    CostTracker,
    CostTrackerConfig,
    DEFAULT_COST_CONFIG,
    PIPELINE_SUBAGENTS,
)


@dataclass
class SessionState:
    """Serializable session state for persistence."""
    session_id: str
    pipeline_id: str
    current_step: int
    is_paused: bool = False
    pause_reason: Optional[str] = None
    conversation_summary: Optional[str] = None


@dataclass
class StepResult:
    """Result from executing a pipeline step."""
    content: str
    structured_output: Optional[dict] = None
    tokens_used: int = 0
    cost: float = 0.0
    was_interrupted: bool = False
    clarification_request: Optional[dict] = None


# Step name mapping
STEP_NAMES = {
    1: "Context & Requirements",
    2: "Risk & Blocker Analysis",
    3: "Implementation Planning",
    4: "Code Implementation",
    5: "Test Writing & Execution",
    6: "Documentation Updates",
    7: "PR Creation & Description",
    8: "Code Review Response",
}

# Map step numbers to subagent names in PIPELINE_SUBAGENTS
STEP_TO_SUBAGENT = {
    1: "context_agent",
    2: "risk_agent",
    3: "planning_agent",
    4: "coding_agent",
    5: "testing_agent",
    6: "docs_agent",
    7: "pr_agent",
    8: "review_agent",
}


class PipelineSession:
    """Manages a persistent ClaudeSDKClient session across pipeline steps.

    This is the core class that enables context persistence. A SINGLE
    ClaudeSDKClient instance is used for ALL steps in the pipeline,
    meaning Claude remembers everything from previous steps.

    Usage:
        session = PipelineSession(
            pipeline_id="abc123",
            codebase_path="/path/to/codebase",
            ticket_context={"key": "PROJ-123", "summary": "Add feature X"},
        )

        # Start the session
        session_id = await session.start()

        # Execute steps - Claude maintains context across all of them
        result1 = await session.execute_step(1, context_agent, context)
        result2 = await session.execute_step(2, risk_agent, context)
        # ... Claude remembers everything from previous steps

        await session.close()
    """

    def __init__(
        self,
        pipeline_id: str,
        codebase_path: str,
        ticket_context: dict,
        model: str = "claude-sonnet-4-20250514",
        cost_config: Optional[CostTrackerConfig] = None,
    ):
        self.pipeline_id = pipeline_id
        self.codebase_path = codebase_path
        self.ticket_context = ticket_context
        self.model = model
        self.cost_config = cost_config or DEFAULT_COST_CONFIG

        self.client: Optional[ClaudeSDKClient] = None
        self.session_id: Optional[str] = None
        self.current_step: int = 0
        self._interrupt_requested: bool = False
        self._is_started: bool = False

    async def start(self) -> str:
        """Initialize the ClaudeSDKClient session.

        Creates a new ClaudeSDKClient and establishes the initial context
        for the pipeline. Returns the session_id for persistence.
        """
        if self._is_started:
            raise RuntimeError("Session already started. Call close() first.")

        options = ClaudeAgentOptions(
            cwd=self.codebase_path,
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Task"],
            model=self.model,
            permission_mode="acceptEdits",
            max_turns=100,  # High limit for multi-step pipeline
            agents=PIPELINE_SUBAGENTS,  # Register subagents for Task tool
            include_partial_messages=True,  # Enable real-time subagent streaming
        )

        self.client = ClaudeSDKClient(options)
        await self.client.__aenter__()
        self._is_started = True

        # Establish initial context with a brief introduction
        initial_prompt = self._build_initial_context_prompt()

        # Send the query first, then receive the response
        await self.client.query(initial_prompt)
        async for msg in self.client.receive_response():
            # Extract session_id if available
            if hasattr(msg, 'session_id') and msg.session_id:
                self.session_id = msg.session_id

        # Generate a session ID if SDK didn't provide one
        if not self.session_id:
            import uuid
            self.session_id = str(uuid.uuid4())

        return self.session_id

    def _build_initial_context_prompt(self) -> str:
        """Build the initial context prompt for the pipeline."""
        ticket_key = self.ticket_context.get('key', 'UNKNOWN')
        summary = self.ticket_context.get('summary', 'No summary')
        description = self.ticket_context.get('description', 'No description')

        return f"""You are the Pipeline Orchestrator for BiAgent, managing a software development pipeline for ticket {ticket_key}.

## Your Role
You coordinate the pipeline by spawning specialized subagents for each step using the Task tool.
You maintain context across all steps and ensure information flows between subagents.

## Ticket Information
- **Key**: {ticket_key}
- **Summary**: {summary}
- **Description**: {description}

## Pipeline Steps & Subagents
You will orchestrate 8 steps, each handled by a specialized subagent:

| Step | Name | Subagent |
|------|------|----------|
| 1 | Context & Requirements | context_agent |
| 2 | Risk & Blocker Analysis | risk_agent |
| 3 | Implementation Planning | planning_agent |
| 4 | Code Implementation | coding_agent |
| 5 | Test Writing & Execution | testing_agent |
| 6 | Documentation Updates | docs_agent |
| 7 | PR Creation | pr_agent |
| 8 | Code Review Response | review_agent |

## How to Use Subagents
For each step, you will receive instructions to spawn a subagent using the Task tool.
Pass the task context to the subagent and summarize its output when it completes.

IMPORTANT: Subagents cannot see your conversation history. You must include all
relevant context from previous steps in your prompt to each subagent.

Acknowledge that you understand your role as orchestrator and are ready to begin.
"""

    async def execute_step(
        self,
        step_number: int,
        agent: BaseAgent,
        context: AgentContext,
        on_token: Optional[Callable[[str], Any]] = None,
        on_tool_call: Optional[Callable[[str, dict, str], Any]] = None,
        on_subagent_tool_call: Optional[Callable[[str, str, str, dict], Any]] = None,
        on_subagent_text: Optional[Callable[[str, str], Any]] = None,
    ) -> StepResult:
        """Execute a pipeline step within the persistent session.

        The key difference from BaseAgent.execute() is that this uses
        the SAME ClaudeSDKClient instance, so Claude remembers all
        previous steps.

        Args:
            step_number: The step number (1-8)
            agent: The BaseAgent instance (used for prompts and parsing)
            context: The AgentContext with ticket and pipeline info
            on_token: Callback for streaming text tokens
            on_tool_call: Callback for tool invocations (tool_name, args, tool_use_id)
            on_subagent_tool_call: Callback for subagent tool invocations
                                   (parent_tool_use_id, tool_name, tool_use_id, args)
            on_subagent_text: Callback for subagent text content
                              (parent_tool_use_id, text)

        Returns:
            StepResult with content, structured_output, and cost info
        """
        if not self.client or not self._is_started:
            raise RuntimeError("Session not started. Call start() first.")

        self.current_step = step_number
        self._interrupt_requested = False

        # Build step prompt using agent's methods
        step_prompt = self._build_step_prompt(step_number, agent, context)

        full_response = ""
        tracker = CostTracker(self.cost_config)

        try:
            # Send the query first, then receive the response
            await self.client.query(step_prompt)
            async for message in self.client.receive_response():
                # Check for interrupt request
                if self._interrupt_requested:
                    return StepResult(
                        content=full_response,
                        structured_output=agent.parse_output(full_response) if full_response else None,
                        was_interrupted=True,
                        **tracker.get_result(),
                    )

                if isinstance(message, AssistantMessage):
                    # Check if this is a subagent message
                    parent_tool_use_id = getattr(message, 'parent_tool_use_id', None)

                    for block in message.content:
                        if isinstance(block, TextBlock):
                            if parent_tool_use_id:
                                # This is SUBAGENT text - route to subagent text handler
                                if on_subagent_text:
                                    result = on_subagent_text(
                                        parent_tool_use_id,
                                        block.text,
                                    )
                                    if asyncio.iscoroutine(result):
                                        await result
                            else:
                                # Main agent text - accumulate and stream
                                text = block.text
                                full_response += text
                                if on_token:
                                    result = on_token(text)
                                    if asyncio.iscoroutine(result):
                                        await result

                        elif isinstance(block, ToolUseBlock):
                            if parent_tool_use_id:
                                # This is a SUBAGENT tool call - route to subagent handler
                                if on_subagent_tool_call:
                                    result = on_subagent_tool_call(
                                        parent_tool_use_id,
                                        block.name,
                                        block.id,
                                        block.input or {},
                                    )
                                    if asyncio.iscoroutine(result):
                                        await result
                            else:
                                # This is a MAIN AGENT tool call
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
        tracker.estimate_from_content(step_prompt, full_response)

        # Calculate cost if not provided
        tracker.calculate_cost()

        return StepResult(
            content=full_response,
            structured_output=agent.parse_output(full_response),
            **tracker.get_result(),
        )

    def _build_step_prompt(
        self,
        step_number: int,
        agent: BaseAgent,
        context: AgentContext,
    ) -> str:
        """Build the prompt for a specific step.

        Instructs the main orchestrator to spawn the appropriate subagent
        via the Task tool for specialized handling of each step.
        """
        step_name = STEP_NAMES.get(step_number, f"Step {step_number}")
        subagent_name = STEP_TO_SUBAGENT.get(step_number)

        # Get the user prompt with task-specific context
        user_prompt = agent.build_user_prompt(context)

        # Build orchestrator instruction to spawn subagent
        return f"""
## Pipeline Step {step_number}: {step_name}

You MUST use the Task tool to spawn the "{subagent_name}" subagent to handle this step.

### Instructions for the Subagent

Pass the following task context to the subagent:

---
{user_prompt}
---

### How to Proceed

1. Use the Task tool with:
   - subagent_type: "{subagent_name}"
   - description: "Execute {step_name}"
   - prompt: Include ALL the task instructions above

2. The subagent will execute the step and return its output.

3. After the subagent completes, summarize its key findings/outputs.

IMPORTANT:
- You have full context from all previous steps in this session.
- The subagent can access files and tools but NOT your conversation history.
- Include any relevant context from previous steps in your prompt to the subagent.
- After receiving the subagent's response, provide a brief summary for the next step.
"""

    async def inject_clarification(self, response: str) -> StepResult:
        """Inject a clarification response into the session.

        Used when the user provides a clarification answer. The session
        continues with full context preserved.

        Args:
            response: The user's clarification response

        Returns:
            StepResult from continued execution
        """
        if not self.client or not self._is_started:
            raise RuntimeError("Session not started. Call start() first.")

        prompt = f"""User provided clarification: {response}

Please continue with the current step, taking this clarification into account.
"""

        full_response = ""
        tracker = CostTracker(self.cost_config)

        # Send the query first, then receive the response
        await self.client.query(prompt)
        async for message in self.client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        full_response += block.text

            elif isinstance(message, ResultMessage):
                tracker.update_from_usage(getattr(message, 'usage', None))
                cost = getattr(message, 'cost_usd', None) or getattr(message, 'cost', None)
                tracker.update_cost(cost)

        tracker.estimate_from_content(prompt, full_response)
        tracker.calculate_cost()

        return StepResult(
            content=full_response,
            **tracker.get_result(),
        )

    async def inject_feedback(self, feedback: str, step_number: int) -> StepResult:
        """Inject user feedback for a completed step.

        Used when the user provides feedback on a step's output.
        The session continues with full context preserved.

        Args:
            feedback: The user's feedback
            step_number: The step the feedback is about

        Returns:
            StepResult from re-execution with feedback
        """
        if not self.client or not self._is_started:
            raise RuntimeError("Session not started. Call start() first.")

        step_name = STEP_NAMES.get(step_number, f"Step {step_number}")

        prompt = f"""User provided feedback on Step {step_number} ({step_name}):

{feedback}

Please revise your output for this step based on the feedback.
Remember all context from previous steps and maintain consistency with your earlier work.
"""

        full_response = ""
        tracker = CostTracker(self.cost_config)

        # Send the query first, then receive the response
        await self.client.query(prompt)
        async for message in self.client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        full_response += block.text

            elif isinstance(message, ResultMessage):
                tracker.update_from_usage(getattr(message, 'usage', None))
                cost = getattr(message, 'cost_usd', None) or getattr(message, 'cost', None)
                tracker.update_cost(cost)

        tracker.estimate_from_content(prompt, full_response)
        tracker.calculate_cost()

        return StepResult(
            content=full_response,
            **tracker.get_result(),
        )

    def request_interrupt(self, reason: str = "user_requested"):
        """Request a pause at the next safe point.

        This sets a flag that will be checked during query iteration.
        The current step will complete its current operation before pausing.
        """
        self._interrupt_requested = True

    def clear_interrupt(self):
        """Clear the interrupt flag to allow resumption."""
        self._interrupt_requested = False

    @property
    def is_interrupted(self) -> bool:
        """Check if an interrupt has been requested."""
        return self._interrupt_requested

    def get_state(self) -> SessionState:
        """Get the current session state for persistence."""
        return SessionState(
            session_id=self.session_id or "",
            pipeline_id=self.pipeline_id,
            current_step=self.current_step,
            is_paused=self._interrupt_requested,
        )

    async def close(self):
        """Close the session and release resources."""
        if self.client and self._is_started:
            try:
                await self.client.__aexit__(None, None, None)
            except Exception:
                pass  # Ignore errors during cleanup
            finally:
                self.client = None
                self._is_started = False

    async def __aenter__(self) -> "PipelineSession":
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    @classmethod
    async def restore(
        cls,
        session_id: str,
        pipeline_id: str,
        codebase_path: str,
        ticket_context: dict,
        conversation_summary: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
    ) -> "PipelineSession":
        """Restore a session from saved state.

        Since we can't truly restore the ClaudeSDKClient's internal state,
        this creates a new session and injects a summary of previous work
        to provide context continuity.

        Args:
            session_id: The original session ID (for tracking)
            pipeline_id: The pipeline ID
            codebase_path: Path to the codebase
            ticket_context: Ticket information
            conversation_summary: Summary of previous work to inject
            model: Model to use

        Returns:
            A new PipelineSession with context restored
        """
        session = cls(
            pipeline_id=pipeline_id,
            codebase_path=codebase_path,
            ticket_context=ticket_context,
            model=model,
        )

        # Start a new underlying session
        await session.start()

        # Inject previous context summary if available
        if conversation_summary:
            restore_prompt = f"""You are resuming a pipeline session that was previously paused.

## Previous Session Summary
{conversation_summary}

Please acknowledge that you understand the previous context and are ready to continue.
Reference this context as needed in subsequent steps.
"""
            await session.client.query(restore_prompt)
            async for _ in session.client.receive_response():
                pass  # Process the restoration

        return session
