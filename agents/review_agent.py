"""
Review Agent (Step 8)

Responds to PR review comments.
"""

from typing import Optional
from .base import BaseAgent, AgentContext


class ReviewAgent(BaseAgent):
    """Agent that responds to code review comments."""

    @property
    def system_prompt(self) -> str:
        return """You are the Code Review Response Agent for BiAgent, an AI-powered development system.

Your role is to address code review feedback on the pull request.

REVIEW RESPONSE PROCESS:
1. Analyze each review comment
2. Understand the feedback
3. Make appropriate code changes
4. Respond to each comment
5. Push updates to the PR

RESPONSE PRINCIPLES:
- Address ALL feedback
- Make clean, focused fixes
- Run tests after changes
- Reply professionally to reviewers
- Ask for clarification if needed

OUTPUT FORMAT:
- Comments addressed (list)
- Code changes made
- New commit SHA
- Replies posted"""

    def build_user_prompt(self, context: AgentContext) -> str:
        comments_text = ""
        if context.review_comments:
            for c in context.review_comments:
                comments_text += f"""
- File: {c.get('file_path', 'N/A')}
  Line: {c.get('line_number', 'N/A')}
  Reviewer: {c.get('reviewer', 'Unknown')}
  Comment: {c.get('comment_body', '')}
"""

        # Build worktree-aware path instructions
        worktree_instructions = ""
        if context.is_worktree:
            worktree_instructions = f"""
IMPORTANT - WORKTREE ISOLATION:
You are working in an isolated git worktree: {context.codebase_path}
All file operations and git commands should use this directory.
"""

        prompt = f"""Please address the following code review comments:

TICKET: {context.ticket_key}
PR: #{context.pr.get('number', 'N/A') if context.pr else 'N/A'}
PR URL: {context.pr.get('url', 'N/A') if context.pr else 'N/A'}
BRANCH: {context.sandbox_branch}
{worktree_instructions}
REVIEW COMMENTS TO ADDRESS:
{comments_text or 'No comments'}

CODEBASE: {context.codebase_path}

Please:
1. Read and understand each comment
2. Make necessary code changes (use relative paths from {context.codebase_path})
3. Run tests to verify
4. Commit and push changes
5. Reply to each comment on GitHub
6. Report what was done
"""

        return prompt

    def parse_output(self, content: str) -> Optional[dict]:
        """Parse review response results."""
        return {
            "raw_output": content,
            "comments_addressed": [],
            "commit_sha": None,
        }
