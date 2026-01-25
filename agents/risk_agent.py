"""
Risk Agent (Step 2)

Analyzes risks and blockers for the implementation.
Generates structured risk cards with severity levels.
"""

import json
import re
from typing import Optional, List, Dict, Any
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

SEVERITY LEVELS:
- HIGH: Must address before coding; could cause significant issues
- MEDIUM: Should address during implementation; moderate impact
- LOW: Monitor and document; minor impact if not addressed

CATEGORIES:
- technical: Architecture, complexity, implementation concerns
- security: Authentication, authorization, data handling, input validation
- performance: Scalability, resource usage, response times
- dependency: External services, libraries, other tickets
- testing: Test coverage, hard-to-test scenarios
- blocker: Cannot proceed until resolved

OUTPUT FORMAT:
You MUST output a JSON code block with the following structure:

```json
{
  "risks": [
    {
      "severity": "high|medium|low",
      "category": "technical|security|performance|dependency|testing|blocker",
      "title": "Brief title",
      "description": "Detailed description of the risk",
      "impact": "What happens if this risk materializes",
      "mitigation": "How to address this risk",
      "is_blocker": false
    }
  ],
  "summary": "Brief overall assessment"
}
```

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

IMPORTANT: Output your analysis as a JSON code block as specified in the system prompt.
"""

        if context.user_feedback:
            prompt += f"""

USER FEEDBACK (incorporate into risk analysis):
{context.user_feedback}
"""

        return prompt

    def parse_output(self, content: str) -> Optional[dict]:
        """Parse risk assessment into structured format with risk cards."""
        # Try to extract JSON from the response
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', content)
        if json_match:
            try:
                parsed = json.loads(json_match.group(1))
                risks = parsed.get("risks", [])

                # Categorize by severity
                high_risks = [r for r in risks if r.get("severity") == "high"]
                medium_risks = [r for r in risks if r.get("severity") == "medium"]
                low_risks = [r for r in risks if r.get("severity") == "low"]
                blockers = [r for r in risks if r.get("is_blocker", False)]

                return {
                    "raw_assessment": content,
                    "risks": risks,
                    "high_risks": high_risks,
                    "medium_risks": medium_risks,
                    "low_risks": low_risks,
                    "blockers": blockers,
                    "summary": parsed.get("summary", ""),
                    "risk_count": {
                        "total": len(risks),
                        "high": len(high_risks),
                        "medium": len(medium_risks),
                        "low": len(low_risks),
                        "blockers": len(blockers)
                    }
                }
            except json.JSONDecodeError:
                pass

        # Fallback to unstructured parsing
        return {
            "raw_assessment": content,
            "risks": [],
            "high_risks": [],
            "medium_risks": [],
            "low_risks": [],
            "blockers": [],
            "summary": "",
            "risk_count": {"total": 0, "high": 0, "medium": 0, "low": 0, "blockers": 0}
        }

    async def save_risk_cards(
        self,
        pipeline_id: str,
        step_id: str,
        risks: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Save parsed risks as risk cards in the database.

        Returns a list of created risk card IDs.
        """
        from db import get_db, generate_id
        from datetime import datetime
        from websocket.manager import broadcast_message

        db = await get_db()
        created_ids = []
        now = datetime.utcnow().isoformat()

        for risk in risks:
            risk_id = generate_id()

            await db.execute("""
                INSERT INTO risk_cards (
                    id, pipeline_id, step_id, severity, category, title, description,
                    impact, mitigation, is_blocker, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                risk_id,
                pipeline_id,
                step_id,
                risk.get("severity", "low"),
                risk.get("category", "technical"),
                risk.get("title", "Unnamed Risk"),
                risk.get("description", ""),
                risk.get("impact"),
                risk.get("mitigation"),
                risk.get("is_blocker", False),
                now
            ))

            created_ids.append(risk_id)

            # Broadcast each risk identified
            await broadcast_message({
                "type": "risk_identified",
                "pipeline_id": pipeline_id,
                "risk_id": risk_id,
                "severity": risk.get("severity", "low"),
                "category": risk.get("category", "technical"),
                "title": risk.get("title", "Unnamed Risk"),
                "is_blocker": risk.get("is_blocker", False)
            })

        await db.commit()
        return created_ids
