"""
Risk Agent (Step 2)

Analyzes risks and blockers for the implementation.
"""

from typing import Optional
from .base import BaseAgent, AgentContext


class RiskAgent(BaseAgent):
    """Agent that analyzes risks and blockers."""

    @property
    def system_prompt(self) -> str:
        return """You are the Risk & Blocker Analysis Agent for BiAgent, an AI-powered development system.

Your role is to identify potential risks, blockers, and concerns before implementation begins.

ANALYSIS AREAS:
1. Technical Risks - complexity, unfamiliar patterns, tight coupling
2. Dependencies - external services, other tickets, team members
3. Testing Risks - hard to test scenarios, missing test coverage
4. Security Concerns - authentication, data handling, input validation
5. Performance Impacts - scalability, resource usage
6. Blockers - missing information, pending decisions, access issues

OUTPUT FORMAT:
Provide a structured risk assessment:
- High Priority Risks (must address before coding)
- Medium Priority Risks (address during implementation)
- Low Priority Risks (monitor and document)
- Blockers (cannot proceed until resolved)
- Mitigations (suggested approaches for each risk)

Be direct and specific. Flag anything that could derail implementation."""

    def build_user_prompt(self, context: AgentContext) -> str:
        # Build worktree-aware path instructions
        worktree_instructions = ""
        if context.is_worktree:
            worktree_instructions = f"""
IMPORTANT - WORKTREE ISOLATION:
You are working in an isolated git worktree: {context.codebase_path}
Use this path when examining the codebase, not paths from Step 1 context.
"""

        prompt = f"""Please analyze risks and blockers for implementing this ticket:

TICKET: {context.ticket_key}
SUMMARY: {context.ticket['summary']}
{worktree_instructions}
CONTEXT FROM STEP 1:
{context.step_1_output.get('content', 'No context available') if context.step_1_output else 'No context available'}

CODEBASE: {context.codebase_path}

Please:
1. Review the ticket requirements and context
2. Use jira_cli to check for blocking tickets
3. Examine the codebase for potential conflicts (use relative paths)
4. Identify all risks and blockers
5. Suggest mitigations for each risk
"""

        if context.user_feedback:
            prompt += f"""

USER FEEDBACK (incorporate into risk analysis):
{context.user_feedback}
"""

        return prompt

    def parse_output(self, content: str) -> Optional[dict]:
        """Parse risk assessment into structured format."""
        return {
            "raw_assessment": content,
            "high_risks": [],
            "medium_risks": [],
            "low_risks": [],
            "blockers": [],
            "mitigations": [],
        }
