"""
BiAgent Agents Module

Provides specialized agents for each pipeline step.
"""

from typing import Optional

from .base import (
    BaseAgent,
    AgentContext,
    AgentResult,
    CostTracker,
    CostTrackerConfig,
    DEFAULT_COST_CONFIG,
    PIPELINE_SUBAGENTS,
)
from .pipeline_session import (
    PipelineSession,
    SessionState,
    StepResult,
    STEP_NAMES,
)
from .context_agent import ContextAgent
from .risk_agent import RiskAgent
from .planning_agent import PlanningAgent
from .coding_agent import CodingAgent
from .testing_agent import TestingAgent
from .docs_agent import DocsAgent
from .pr_agent import PRAgent
from .review_agent import ReviewAgent


AGENT_CLASSES = {
    "context": ContextAgent,
    "risk": RiskAgent,
    "planning": PlanningAgent,
    "coding": CodingAgent,
    "testing": TestingAgent,
    "docs": DocsAgent,
    "pr": PRAgent,
    "review": ReviewAgent,
}


def create_agent(
    agent_type: str,
    model: str,
    max_tokens: int,
    tools: list[str],
    cost_config: Optional[CostTrackerConfig] = None,
) -> BaseAgent:
    """Create an agent of the specified type.

    Args:
        agent_type: The type of agent to create
        model: The model to use
        max_tokens: Maximum tokens for the response
        tools: List of tools the agent can use
        cost_config: Optional cost tracking configuration. If None, uses default.
                     Set CostTrackerConfig(enabled=False) to disable cost tracking.
    """
    agent_class = AGENT_CLASSES.get(agent_type)
    if not agent_class:
        raise ValueError(f"Unknown agent type: {agent_type}")

    return agent_class(
        model=model,
        max_tokens=max_tokens,
        tools=tools,
        cost_config=cost_config,
    )


__all__ = [
    # Base classes
    "BaseAgent",
    "AgentContext",
    "AgentResult",
    # Cost tracking
    "CostTracker",
    "CostTrackerConfig",
    "DEFAULT_COST_CONFIG",
    # Pipeline session (persistent ClaudeSDKClient)
    "PipelineSession",
    "SessionState",
    "StepResult",
    "STEP_NAMES",
    "PIPELINE_SUBAGENTS",
    # Factory
    "create_agent",
    # Agent implementations
    "ContextAgent",
    "RiskAgent",
    "PlanningAgent",
    "CodingAgent",
    "TestingAgent",
    "DocsAgent",
    "PRAgent",
    "ReviewAgent",
]
