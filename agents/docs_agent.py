"""
Docs Agent (Step 6)

Updates documentation based on code changes.
"""

from typing import Optional
from .base import BaseAgent, AgentContext


class DocsAgent(BaseAgent):
    """Agent that updates documentation."""

    @property
    def system_prompt(self) -> str:
        return """You are the Documentation Updates Agent for BiAgent, an AI-powered development system.

Your role is to update documentation to reflect the code changes made in Steps 4-5.

DOCUMENTATION TASKS:
1. Update README if needed
2. Update API documentation
3. Update inline code comments
4. Update changelog/release notes
5. Update any relevant guides

DOCUMENTATION PRINCIPLES:
- Keep documentation concise and accurate
- Follow existing documentation style
- Update examples to reflect new functionality
- Remove outdated information
- Add migration notes if breaking changes

OUTPUT FORMAT:
- Documentation files updated
- Summary of documentation changes
- Any new documentation files created
- Suggested follow-up documentation tasks"""

    def build_user_prompt(self, context: AgentContext) -> str:
        # Build worktree-aware path instructions
        worktree_instructions = ""
        if context.is_worktree:
            worktree_instructions = f"""
IMPORTANT - WORKTREE ISOLATION:
You are working in an isolated git worktree: {context.codebase_path}
Use relative paths for all file operations.
"""

        prompt = f"""Please update documentation for the implementation:

TICKET: {context.ticket_key}
SUMMARY: {context.ticket['summary']}
{worktree_instructions}
IMPLEMENTATION SUMMARY (Step 4):
{context.step_4_output.get('content', '')[:2000] if context.step_4_output else 'No summary'}

TEST SUMMARY (Step 5):
{context.step_5_output.get('content', '')[:1000] if context.step_5_output else 'No tests'}

CODEBASE: {context.codebase_path}

Please:
1. Review the changes made
2. Identify documentation that needs updates
3. Update relevant documentation files (use relative paths)
4. Ensure examples are accurate
5. Report what was updated
"""

        if context.user_feedback:
            prompt += f"""

USER FEEDBACK:
{context.user_feedback}
"""

        return prompt

    def parse_output(self, content: str) -> Optional[dict]:
        """Parse documentation results."""
        return {
            "raw_summary": content,
            "files_updated": [],
            "files_created": [],
        }
