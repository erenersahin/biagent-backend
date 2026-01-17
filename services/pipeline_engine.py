"""
Pipeline Execution Engine

Orchestrates the 8-step agent pipeline for ticket resolution.
"""

import asyncio
from datetime import datetime
from typing import Optional

from db import get_db, generate_id, json_dumps, json_loads
from config import settings, get_step_config, STEP_CONFIGS
from websocket.manager import broadcast_message
from agents import create_agent, AgentContext


class PipelineEngine:
    """Engine for executing pipelines."""

    def __init__(
        self,
        pipeline_id: str,
        feedback: Optional[str] = None,
        guidance: Optional[str] = None,
    ):
        self.pipeline_id = pipeline_id
        self.feedback = feedback
        self.guidance = guidance
        self._db = None

    async def get_db(self):
        """Get database connection."""
        if self._db is None:
            self._db = await get_db()
        return self._db

    async def run(self):
        """Run the pipeline from current step."""
        db = await self.get_db()

        # Get pipeline
        pipeline = await db.fetchone(
            "SELECT * FROM pipelines WHERE id = ?", (self.pipeline_id,)
        )
        if not pipeline:
            return

        current_step = pipeline["current_step"]

        # Broadcast start
        await broadcast_message({
            "type": "pipeline_started" if current_step == 1 else "pipeline_resumed",
            "pipeline_id": self.pipeline_id,
            "ticket_key": pipeline["ticket_key"],
        })

        # Execute steps sequentially
        while current_step <= 8:
            # Check for pause request
            pipeline = await db.fetchone(
                "SELECT pause_requested, status FROM pipelines WHERE id = ?",
                (self.pipeline_id,)
            )

            if pipeline["pause_requested"]:
                await self._pause_pipeline(current_step)
                return

            if pipeline["status"] not in ("running",):
                return

            # Execute step
            success = await self._execute_step(current_step)

            if not success:
                # Step failed
                return

            # Move to next step
            current_step += 1

            if current_step <= 8:
                await self._transition_to_step(current_step)

        # Pipeline complete
        await self._complete_pipeline()

    async def _execute_step(self, step_number: int) -> bool:
        """Execute a single step."""
        db = await self.get_db()
        config = get_step_config(step_number)

        # Get step record
        step = await db.fetchone("""
            SELECT * FROM pipeline_steps
            WHERE pipeline_id = ? AND step_number = ?
        """, (self.pipeline_id, step_number))

        if not step:
            return False

        now = datetime.utcnow().isoformat()

        # Broadcast step start
        await broadcast_message({
            "type": "step_started",
            "pipeline_id": self.pipeline_id,
            "step": step_number,
            "step_name": config["name"],
        })

        try:
            # Build agent context
            context = await self._build_agent_context(step_number)

            # Add feedback if provided
            if self.feedback and step_number == (await db.fetchone(
                "SELECT current_step FROM pipelines WHERE id = ?",
                (self.pipeline_id,)
            ))["current_step"]:
                context["user_feedback"] = self.feedback
                self.feedback = None  # Clear after use

            if self.guidance:
                context["user_guidance"] = self.guidance
                self.guidance = None

            # Create and run agent
            agent = create_agent(
                agent_type=config["agent_type"],
                model=config["model"],
                max_tokens=config["max_tokens"],
                tools=config["tools"],
            )

            # Execute with streaming
            result = await agent.execute(
                context=AgentContext(**context),
                on_token=lambda token: self._stream_token(step_number, token),
                on_tool_call=lambda tool, args: self._log_tool_call(step["id"], tool, args),
            )

            # Save output
            output_id = generate_id()
            await db.execute("""
                INSERT INTO step_outputs (id, step_id, output_type, content, content_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                output_id,
                step["id"],
                config["output_type"],
                result.get("content", ""),
                json_dumps(result.get("structured_output")),
                now,
            ))

            # Update step as completed
            completed_at = datetime.utcnow().isoformat()
            await db.execute("""
                UPDATE pipeline_steps
                SET status = 'completed', completed_at = ?, tokens_used = ?, cost = ?
                WHERE id = ?
            """, (completed_at, result.get("tokens_used", 0), result.get("cost", 0), step["id"]))

            # Update pipeline totals
            await db.execute("""
                UPDATE pipelines
                SET total_tokens = total_tokens + ?, total_cost = total_cost + ?
                WHERE id = ?
            """, (result.get("tokens_used", 0), result.get("cost", 0), self.pipeline_id))

            await db.commit()

            # Broadcast completion with output for UI display
            await broadcast_message({
                "type": "step_completed",
                "pipeline_id": self.pipeline_id,
                "step": step_number,
                "next_step": step_number + 1 if step_number < 8 else None,
                "tokens_used": result.get("tokens_used", 0),
                "cost": result.get("cost", 0),
                "output": result.get("content", "")[:5000],  # Truncate for websocket
            })

            return True

        except Exception as e:
            # Step failed
            await db.execute("""
                UPDATE pipeline_steps
                SET status = 'failed', error_message = ?
                WHERE id = ?
            """, (str(e), step["id"]))

            await db.execute("""
                UPDATE pipelines SET status = 'failed' WHERE id = ?
            """, (self.pipeline_id,))

            await db.commit()

            await broadcast_message({
                "type": "pipeline_failed",
                "pipeline_id": self.pipeline_id,
                "step": step_number,
                "error": str(e),
            })

            return False

    async def _build_agent_context(self, step_number: int) -> dict:
        """Build context for agent execution."""
        db = await self.get_db()

        # Get pipeline and ticket
        pipeline = await db.fetchone(
            "SELECT * FROM pipelines WHERE id = ?", (self.pipeline_id,)
        )

        ticket = await db.fetchone(
            "SELECT * FROM tickets WHERE key = ?", (pipeline["ticket_key"],)
        )

        context = {
            "pipeline_id": self.pipeline_id,
            "ticket_key": pipeline["ticket_key"],
            "ticket": {
                "key": ticket["key"],
                "summary": ticket["summary"],
                "description": ticket["description"],
                "status": ticket["status"],
                "priority": ticket["priority"],
            },
            "codebase_path": settings.codebase_path,
            "sandbox_branch": f"{settings.sandbox_branch_prefix}{pipeline['ticket_key']}",
        }

        # Add outputs from previous steps
        for prev_step in range(1, step_number):
            step_record = await db.fetchone("""
                SELECT id FROM pipeline_steps
                WHERE pipeline_id = ? AND step_number = ?
            """, (self.pipeline_id, prev_step))

            if step_record:
                output = await db.fetchone("""
                    SELECT * FROM step_outputs
                    WHERE step_id = ?
                    ORDER BY created_at DESC LIMIT 1
                """, (step_record["id"],))

                if output:
                    context[f"step_{prev_step}_output"] = {
                        "type": output["output_type"],
                        "content": output["content"],
                        "structured": json_loads(output["content_json"]) if output["content_json"] else None,
                    }

        return context

    async def _stream_token(self, step_number: int, token: str):
        """Stream token to clients."""
        await broadcast_message({
            "type": "token",
            "pipeline_id": self.pipeline_id,
            "step": step_number,
            "token": token,
        })

    async def _log_tool_call(self, step_id: str, tool: str, args: dict):
        """Log tool call to database."""
        db = await self.get_db()

        tool_call_id = generate_id()
        await db.execute("""
            INSERT INTO tool_calls (id, step_id, tool_name, arguments, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (tool_call_id, step_id, tool, json_dumps(args), datetime.utcnow().isoformat()))
        await db.commit()

        await broadcast_message({
            "type": "tool_call_started",
            "pipeline_id": self.pipeline_id,
            "tool": tool,
            "arguments": args,
        })

    async def _transition_to_step(self, step_number: int):
        """Transition to next step."""
        db = await self.get_db()
        now = datetime.utcnow().isoformat()

        await db.execute("""
            UPDATE pipelines SET current_step = ? WHERE id = ?
        """, (step_number, self.pipeline_id))

        await db.execute("""
            UPDATE pipeline_steps
            SET status = 'running', started_at = ?
            WHERE pipeline_id = ? AND step_number = ?
        """, (now, self.pipeline_id, step_number))

        await db.commit()

    async def _pause_pipeline(self, current_step: int):
        """Pause the pipeline."""
        db = await self.get_db()
        now = datetime.utcnow().isoformat()

        await db.execute("""
            UPDATE pipelines
            SET status = 'paused', paused_at = ?, pause_requested = FALSE
            WHERE id = ?
        """, (now, self.pipeline_id))

        await db.execute("""
            UPDATE pipeline_steps
            SET status = 'paused'
            WHERE pipeline_id = ? AND step_number = ?
        """, (self.pipeline_id, current_step))

        await db.commit()

        await broadcast_message({
            "type": "pipeline_paused",
            "pipeline_id": self.pipeline_id,
            "step": current_step,
        })

    async def _complete_pipeline(self):
        """Mark pipeline as complete."""
        db = await self.get_db()
        now = datetime.utcnow().isoformat()

        # Get totals
        pipeline = await db.fetchone(
            "SELECT total_tokens, total_cost FROM pipelines WHERE id = ?",
            (self.pipeline_id,)
        )

        await db.execute("""
            UPDATE pipelines SET status = 'completed', completed_at = ? WHERE id = ?
        """, (now, self.pipeline_id))

        await db.commit()

        await broadcast_message({
            "type": "pipeline_completed",
            "pipeline_id": self.pipeline_id,
            "total_tokens": pipeline["total_tokens"],
            "total_cost": pipeline["total_cost"],
        })

    async def run_review_step(self, comments: list[dict]):
        """Run the review agent for PR comments."""
        db = await self.get_db()

        # Get step 8
        step = await db.fetchone("""
            SELECT * FROM pipeline_steps
            WHERE pipeline_id = ? AND step_number = 8
        """, (self.pipeline_id,))

        if not step:
            return

        # Update step status
        now = datetime.utcnow().isoformat()
        await db.execute("""
            UPDATE pipeline_steps
            SET status = 'running', started_at = ?, iteration_count = iteration_count + 1
            WHERE id = ?
        """, (now, step["id"]))

        await db.execute("""
            UPDATE pipelines SET status = 'running' WHERE id = ?
        """, (self.pipeline_id,))

        await db.commit()

        # Build context with comments
        context = await self._build_agent_context(8)
        context["review_comments"] = comments

        # Get PR info
        pr = await db.fetchone("""
            SELECT * FROM pull_requests WHERE pipeline_id = ?
        """, (self.pipeline_id,))

        if pr:
            context["pr"] = {
                "number": pr["pr_number"],
                "url": pr["pr_url"],
                "branch": pr["branch"],
            }

        # Execute review agent
        config = get_step_config(8)
        agent = create_agent(
            agent_type=config["agent_type"],
            model=config["model"],
            max_tokens=config["max_tokens"],
            tools=config["tools"],
        )

        try:
            result = await agent.execute(
                context=AgentContext(**context),
                on_token=lambda token: self._stream_token(8, token),
                on_tool_call=lambda tool, args: self._log_tool_call(step["id"], tool, args),
            )

            # Mark comments as processed
            for comment in comments:
                await db.execute("""
                    UPDATE review_comments
                    SET processed = TRUE, processed_at = ?, agent_response = ?
                    WHERE id = ?
                """, (now, result.get("content", "")[:500], comment["id"]))

            # Save iteration
            pr_record = await db.fetchone(
                "SELECT id FROM pull_requests WHERE pipeline_id = ?",
                (self.pipeline_id,)
            )
            if pr_record:
                iteration_id = generate_id()
                await db.execute("""
                    INSERT INTO review_iterations
                    (id, pr_id, iteration_number, comments_received, comments_addressed,
                     commit_sha, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    iteration_id,
                    pr_record["id"],
                    step["iteration_count"] + 1,
                    len(comments),
                    len(comments),
                    result.get("commit_sha"),
                    now,
                ))

            # Back to waiting
            await db.execute("""
                UPDATE pipeline_steps SET status = 'waiting' WHERE id = ?
            """, (step["id"],))

            await db.execute("""
                UPDATE pipelines SET status = 'waiting_for_review' WHERE id = ?
            """, (self.pipeline_id,))

            await db.commit()

            await broadcast_message({
                "type": "review_responded",
                "pipeline_id": self.pipeline_id,
                "iteration": step["iteration_count"] + 1,
                "comments_addressed": len(comments),
            })

        except Exception as e:
            await db.execute("""
                UPDATE pipeline_steps SET status = 'failed', error_message = ? WHERE id = ?
            """, (str(e), step["id"]))

            await db.execute("""
                UPDATE pipelines SET status = 'failed' WHERE id = ?
            """, (self.pipeline_id,))

            await db.commit()

            await broadcast_message({
                "type": "pipeline_failed",
                "pipeline_id": self.pipeline_id,
                "step": 8,
                "error": str(e),
            })
