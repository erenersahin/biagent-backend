"""
Planning Agent (Step 3)

Creates implementation plan for the ticket.
"""

from typing import Optional
from .base import BaseAgent, AgentContext


class PlanningAgent(BaseAgent):
    """Agent that creates implementation plans."""

    @property
    def system_prompt(self) -> str:
        return """You are the Implementation Planning Agent for BiAgent, an AI-powered development system.

Your role is to create a detailed, actionable implementation plan based on context and risk analysis.

PLAN COMPONENTS:
1. Approach Summary - high-level approach and rationale
2. File Changes - specific files to create, modify, or delete
3. Implementation Steps - ordered list of coding tasks
4. Testing Strategy - what tests to write and why
5. Documentation Needs - what docs to update

PLANNING PRINCIPLES:
- Follow existing patterns in the codebase
- Prefer small, focused changes
- Consider backwards compatibility
- Plan for edge cases identified in risk analysis
- Include rollback considerations

OUTPUT FORMAT:
Provide a structured implementation plan:
- Approach (1-2 paragraphs)
- Files to Change (table with file path, action, description)
- Implementation Steps (numbered, detailed steps)
- Testing Plan (specific tests to add)
- Documentation Updates (files to update)

Be specific enough that the Coding Agent can execute without ambiguity."""

    def build_user_prompt(self, context: AgentContext) -> str:
        prompt = f"""Please create an implementation plan for this ticket:

TICKET: {context.ticket_key}
SUMMARY: {context.ticket['summary']}

CONTEXT FROM STEP 1:
{context.step_1_output.get('content', 'No context') if context.step_1_output else 'No context'}

RISKS FROM STEP 2:
{context.step_2_output.get('content', 'No risks') if context.step_2_output else 'No risks'}

CODEBASE: {context.codebase_path}
BRANCH: {context.sandbox_branch}

Please:
1. Review the context and risk analysis
2. Explore the codebase to understand patterns
3. Design a clear implementation approach
4. Create detailed, actionable steps
5. Plan the testing strategy
"""

        if context.user_feedback:
            prompt += f"""

USER FEEDBACK (incorporate into plan):
{context.user_feedback}
"""

        return prompt

    def parse_output(self, content: str) -> Optional[dict]:
        """Parse plan into structured format."""
        return {
            "raw_plan": content,
            "approach": "",
            "files_to_change": [],
            "implementation_steps": [],
            "testing_plan": [],
            "documentation_updates": [],
        }
