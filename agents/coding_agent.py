"""
Coding Agent (Step 4)

Implements the code changes according to the plan.
"""

from typing import Optional
from .base import BaseAgent, AgentContext


class CodingAgent(BaseAgent):
    """Agent that implements code changes."""

    @property
    def system_prompt(self) -> str:
        return """You are the Code Implementation Agent for BiAgent, an AI-powered development system.

Your role is to implement the code changes according to the implementation plan from Step 3.

CODING GUIDELINES:
1. Follow the implementation plan exactly
2. Match existing code style and patterns
3. Write clean, readable code
4. Add appropriate comments for complex logic
5. Handle errors gracefully
6. Consider edge cases from risk analysis

IMPLEMENTATION PROCESS:
1. Read relevant existing files first
2. Create sandbox branch if needed
3. Implement changes file by file
4. Run type checks / linting after changes
5. Verify no obvious errors

OUTPUT FORMAT:
- Summary of changes made
- List of files created
- List of files modified
- Any deviations from plan (with rationale)
- Issues encountered

DO NOT:
- Skip steps in the plan
- Make changes outside the scope
- Leave TODO comments for critical functionality
- Ignore type errors or linting issues"""

    def build_user_prompt(self, context: AgentContext) -> str:
        prompt = f"""Please implement the code changes for this ticket:

TICKET: {context.ticket_key}
SUMMARY: {context.ticket['summary']}

IMPLEMENTATION PLAN FROM STEP 3:
{context.step_3_output.get('content', 'No plan') if context.step_3_output else 'No plan'}

CONTEXT FROM STEP 1:
{context.step_1_output.get('content', 'No context')[:2000] if context.step_1_output else 'No context'}

RISKS TO ADDRESS:
{context.step_2_output.get('content', 'No risks')[:1000] if context.step_2_output else 'No risks'}

CODEBASE: {context.codebase_path}
BRANCH: {context.sandbox_branch}

Please:
1. Ensure you're on the sandbox branch: {context.sandbox_branch}
2. Read the files you need to modify
3. Implement changes according to the plan
4. Run type checks to verify
5. Report what was created/modified
"""

        if context.user_feedback:
            prompt += f"""

USER FEEDBACK (important - adjust implementation accordingly):
{context.user_feedback}
"""

        return prompt

    def parse_output(self, content: str) -> Optional[dict]:
        """Parse implementation results."""
        return {
            "raw_summary": content,
            "files_created": [],
            "files_modified": [],
            "deviations": [],
            "issues": [],
        }
