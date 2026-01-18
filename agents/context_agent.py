"""
Context Agent (Step 1)

Gathers context and requirements for the ticket.
"""

import re
import json
from typing import Optional, List, Dict
from .base import BaseAgent, AgentContext
from config import settings


class ContextAgent(BaseAgent):
    """Agent that gathers context and requirements."""

    @property
    def system_prompt(self) -> str:
        base_prompt = """You are the Context & Requirements Agent for BiAgent, an AI-powered development system.

Your role is to gather all necessary context for implementing a JIRA ticket. You will:

1. Analyze the ticket details (summary, description, acceptance criteria)
2. Find related tickets and dependencies
3. Search the codebase for relevant files and patterns
4. Identify key requirements and constraints
5. Fetch any relevant documentation from Notion"""

        # Add repo detection instructions if worktrees are enabled
        if settings.worktree_enabled:
            base_prompt += """
6. Determine which repositories are affected by this ticket"""

        base_prompt += """

OUTPUT FORMAT:
Your output should be a comprehensive context summary that includes:
- Ticket summary and key requirements
- Related tickets and their status
- Relevant codebase files identified
- Technical constraints discovered
- Any questions or ambiguities found"""

        if settings.worktree_enabled:
            base_prompt += """

IMPORTANT - AFFECTED REPOSITORIES:
The codebase may contain multiple git repositories. You must analyze which repositories
need to be modified to implement this ticket.

At the END of your output, include a JSON block with the affected repos:

```json
{
  "affected_repos": [
    {"name": "repo-name-1", "reason": "Brief reason why this repo is affected"},
    {"name": "repo-name-2", "reason": "Brief reason why this repo is affected"}
  ]
}
```

If you cannot determine affected repos from the ticket, list all repos you find in the codebase path."""

        base_prompt += """

Be thorough but concise. Focus on information that will help the subsequent agents."""

        return base_prompt

    def build_user_prompt(self, context: AgentContext) -> str:
        prompt = f"""Please gather context for the following JIRA ticket:

TICKET: {context.ticket_key}
SUMMARY: {context.ticket['summary']}
STATUS: {context.ticket['status']}
PRIORITY: {context.ticket.get('priority', 'Not set')}

DESCRIPTION:
{context.ticket.get('description', 'No description provided')}

CODEBASE PATH: {context.codebase_path}
TARGET BRANCH: {context.sandbox_branch}

Please:
1. Use the jira_cli tool to get full ticket details and related tickets
2. Search the codebase for relevant files using file_read and file_list
3. Identify patterns and conventions in the existing code
4. Summarize all findings for the next agents
"""

        if context.user_feedback:
            prompt += f"""

USER FEEDBACK (address this in your analysis):
{context.user_feedback}
"""

        return prompt

    def parse_output(self, content: str) -> Optional[dict]:
        """Parse context output into structured format."""
        result = {
            "raw_context": content,
            "ticket_key": None,  # Would parse from content
            "relevant_files": [],  # Would parse from content
            "requirements": [],  # Would parse from content
            "affected_repos": [],  # Extracted repo list for worktree creation
        }

        # Extract affected_repos from JSON block if present
        if settings.worktree_enabled:
            affected_repos = self._extract_affected_repos(content)
            if affected_repos:
                result["affected_repos"] = affected_repos

        return result

    def _extract_affected_repos(self, content: str) -> List[Dict[str, str]]:
        """Extract affected_repos from the output content."""
        # Try to find JSON block with affected_repos
        patterns = [
            r'```json\s*\n?\s*(\{[^`]*"affected_repos"[^`]*\})\s*\n?\s*```',
            r'(\{[^{}]*"affected_repos"\s*:\s*\[[^\]]*\][^{}]*\})',
        ]

        for pattern in patterns:
            match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            if match:
                try:
                    data = json.loads(match.group(1))
                    repos = data.get("affected_repos", [])
                    if repos and isinstance(repos, list):
                        return repos
                except (json.JSONDecodeError, KeyError):
                    continue

        return []
