"""
PR Agent (Step 7)

Creates pull request with description.
"""

from typing import Optional
from .base import BaseAgent, AgentContext


class PRAgent(BaseAgent):
    """Agent that creates pull requests."""

    @property
    def system_prompt(self) -> str:
        return """You are the PR Creation Agent for BiAgent, an AI-powered development system.

Your role is to create a well-documented pull request for the implementation.

PR COMPONENTS:
1. Title - Clear, concise, follows conventions
2. Description - What, why, how
3. Testing notes - How to test the changes
4. Screenshots - If UI changes (describe what to capture)
5. Checklist - Standard PR checklist items

PR DESCRIPTION FORMAT:
## Summary
Brief description of changes

## Changes
- Bullet points of specific changes

## Testing
How to test the changes

## Related
- JIRA ticket link
- Related PRs

PROCESS:
1. Commit all changes with meaningful message
2. Push branch to remote
3. Create PR using gh cli
4. Add labels and reviewers if configured"""

    def build_user_prompt(self, context: AgentContext) -> str:
        prompt = f"""Please create a pull request for the implementation:

TICKET: {context.ticket_key}
SUMMARY: {context.ticket['summary']}

IMPLEMENTATION (Step 4):
{context.step_4_output.get('content', '')[:2000] if context.step_4_output else ''}

TESTS (Step 5):
{context.step_5_output.get('content', '')[:1000] if context.step_5_output else ''}

DOCS (Step 6):
{context.step_6_output.get('content', '')[:500] if context.step_6_output else ''}

BRANCH: {context.sandbox_branch}
CODEBASE: {context.codebase_path}

Please:
1. Stage all changes
2. Create a meaningful commit message
3. Push to remote
4. Create PR with comprehensive description
5. Report PR URL and details
"""

        if context.user_feedback:
            prompt += f"""

USER FEEDBACK:
{context.user_feedback}
"""

        return prompt

    def parse_output(self, content: str) -> Optional[dict]:
        """Parse PR creation results."""
        return {
            "raw_output": content,
            "pr_number": None,
            "pr_url": None,
            "commit_sha": None,
        }
