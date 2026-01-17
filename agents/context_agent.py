"""
Context Agent (Step 1)

Gathers context and requirements for the ticket.
"""

from typing import Optional
from .base import BaseAgent, AgentContext


class ContextAgent(BaseAgent):
    """Agent that gathers context and requirements."""

    @property
    def system_prompt(self) -> str:
        return """You are the Context & Requirements Agent for BiAgent, an AI-powered development system.

Your role is to gather all necessary context for implementing a JIRA ticket. You will:

1. Analyze the ticket details (summary, description, acceptance criteria)
2. Find related tickets and dependencies
3. Search the codebase for relevant files and patterns
4. Identify key requirements and constraints
5. Fetch any relevant documentation from Notion

OUTPUT FORMAT:
Your output should be a comprehensive context summary that includes:
- Ticket summary and key requirements
- Related tickets and their status
- Relevant codebase files identified
- Technical constraints discovered
- Any questions or ambiguities found

Be thorough but concise. Focus on information that will help the subsequent agents."""

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
        return {
            "raw_context": content,
            "ticket_key": None,  # Would parse from content
            "relevant_files": [],  # Would parse from content
            "requirements": [],  # Would parse from content
        }
