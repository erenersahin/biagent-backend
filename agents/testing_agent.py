"""
Testing Agent (Step 5)

Writes and executes tests for the implementation.
"""

from typing import Optional
from .base import BaseAgent, AgentContext


class TestingAgent(BaseAgent):
    """Agent that writes and runs tests."""

    @property
    def system_prompt(self) -> str:
        return """You are the Test Writing & Execution Agent for BiAgent, an AI-powered development system.

Your role is to write comprehensive tests for the code changes made in Step 4.

TESTING GUIDELINES:
1. Write tests that verify the requirements are met
2. Test edge cases identified in risk analysis
3. Follow existing test patterns in the codebase
4. Include both unit tests and integration tests where appropriate
5. Test error handling paths

TEST CATEGORIES:
- Unit Tests: Individual functions/methods
- Integration Tests: Component interactions
- Edge Cases: Boundary conditions, error states
- Regression Tests: Ensure existing functionality works

PROCESS:
1. Read the implementation from Step 4
2. Identify test cases needed
3. Write test files
4. Run tests to verify they pass
5. Fix any issues found

OUTPUT FORMAT:
- Test summary (what's being tested)
- Test files created
- Test results (pass/fail)
- Coverage summary
- Issues found and fixed"""

    def build_user_prompt(self, context: AgentContext) -> str:
        # Build worktree-aware path instructions
        worktree_instructions = ""
        if context.is_worktree:
            worktree_instructions = f"""
IMPORTANT - WORKTREE ISOLATION:
You are working in an isolated git worktree: {context.codebase_path}
Use relative paths for all file operations, not absolute paths from earlier step outputs.
"""

        prompt = f"""Please write and execute tests for the implementation:

TICKET: {context.ticket_key}
SUMMARY: {context.ticket['summary']}
{worktree_instructions}
IMPLEMENTATION FROM STEP 4:
{context.step_4_output.get('content', 'No implementation')[:3000] if context.step_4_output else 'No implementation'}

CONTEXT:
{context.step_1_output.get('content', '')[:1000] if context.step_1_output else ''}

RISKS TO TEST:
{context.step_2_output.get('content', '')[:1000] if context.step_2_output else ''}

CODEBASE: {context.codebase_path}
BRANCH: {context.sandbox_branch}

Please:
1. Review the implementation changes
2. Identify what needs to be tested
3. Write comprehensive tests (use relative file paths)
4. Run the tests
5. Fix any failing tests
6. Report results
"""

        if context.user_feedback:
            prompt += f"""

USER FEEDBACK (adjust testing accordingly):
{context.user_feedback}
"""

        return prompt

    def parse_output(self, content: str) -> Optional[dict]:
        """Parse test results."""
        return {
            "raw_results": content,
            "tests_written": [],
            "tests_passed": 0,
            "tests_failed": 0,
            "coverage": None,
            "issues_found": [],
        }
