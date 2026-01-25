"""
Pipeline Execution Engine

Orchestrates the 8-step agent pipeline for ticket resolution.
Uses PipelineSession for persistent context across all steps.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

from db import get_db, generate_id, json_dumps, json_loads
from config import settings, get_step_config, STEP_CONFIGS
from websocket.manager import broadcast_message
from agents import create_agent, AgentContext, PipelineSession
from . import session_store

# Get max steps from config
MAX_STEPS = settings.max_steps


class PipelineEngine:
    """Engine for executing pipelines using PipelineSession.

    Uses a persistent ClaudeSDKClient session across all steps,
    enabling context persistence where Claude remembers everything
    from previous steps.
    """

    def __init__(
        self,
        pipeline_id: str,
        feedback: Optional[str] = None,
        guidance: Optional[str] = None,
        clarification_answer: Optional[str] = None,
    ):
        self.pipeline_id = pipeline_id
        self.feedback = feedback
        self.guidance = guidance
        self.clarification_answer = clarification_answer  # Answer to a clarification request
        self._db = None
        self._step_events: dict[int, list] = {}  # Track events per step for chronological storage
        self._worktree_manager = None  # Initialized if worktrees enabled
        self._worktree_session = None  # Current worktree session
        self._worktree_paths: Dict[str, str] = {}  # repo_name -> worktree_path mapping
        self._session: Optional[PipelineSession] = None  # Persistent Claude session

    async def get_db(self):
        """Get database connection."""
        if self._db is None:
            self._db = await get_db()
        return self._db

    async def run(self):
        """Run the pipeline from current step with persistent session."""
        db = await self.get_db()

        # Get pipeline
        pipeline = await db.fetchone(
            "SELECT * FROM pipelines WHERE id = ?", (self.pipeline_id,)
        )
        if not pipeline:
            return

        current_step = pipeline["current_step"]

        # Load existing worktree session if resuming (step > 1)
        if current_step > 1 and settings.worktree_enabled and not self._worktree_paths:
            await self._load_existing_worktree_session()

        # Create or restore PipelineSession
        try:
            if current_step == 1:
                self._session = await self._create_new_session(pipeline)
            else:
                self._session = await self._restore_session(pipeline)
        except Exception as e:
            logger.error(f"Failed to create/restore session: {e}")
            # Fall back to stateless execution if session creation fails
            self._session = None

        # Broadcast start
        await broadcast_message({
            "type": "pipeline_started" if current_step == 1 else "pipeline_resumed",
            "pipeline_id": self.pipeline_id,
            "ticket_key": pipeline["ticket_key"],
        })

        try:
            # Execute steps sequentially (up to MAX_STEPS)
            while current_step <= MAX_STEPS:
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

                # Update session progress after successful step
                if self._session:
                    await session_store.update_session_progress(
                        self.pipeline_id,
                        current_step,
                    )

                # Move to next step
                current_step += 1

                if current_step <= MAX_STEPS:
                    await self._transition_to_step(current_step)

            # Pipeline complete
            await self._complete_pipeline()

        finally:
            # Clean up session on completion or error
            if self._session:
                await self._session.close()
                self._session = None

    async def _execute_step(self, step_number: int) -> bool:
        """Execute a single step using PipelineSession when available."""
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
            context["step_id"] = step["id"]  # Pass step_id for clarification tracking

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

            # Add clarification answer if resuming from a clarification
            if self.clarification_answer:
                context["clarification_answer"] = self.clarification_answer
                self.clarification_answer = None  # Clear after use

            # Create agent (used for prompts and parsing)
            agent = create_agent(
                agent_type=config["agent_type"],
                model=config["model"],
                max_tokens=config["max_tokens"],
                tools=config["tools"],
            )

            # Execute step - use PipelineSession if available for context persistence
            if self._session:
                # Use persistent session - Claude remembers all previous steps
                step_result = await self._session.execute_step(
                    step_number=step_number,
                    agent=agent,
                    context=AgentContext(**context),
                    on_token=lambda token: self._stream_token(step_number, token),
                    on_tool_call=lambda tool, args, tool_use_id, sn=step_number, sid=step["id"]: self._log_tool_call(sid, sn, tool, args, tool_use_id),
                    on_subagent_tool_call=lambda parent_id, tool, tool_id, args, sn=step_number, sid=step["id"]: self._log_subagent_tool_call(sid, sn, parent_id, tool, tool_id, args),
                    on_subagent_text=lambda parent_id, text, sn=step_number: self._stream_subagent_text(sn, parent_id, text),
                )
                result = {
                    "content": step_result.content,
                    "structured_output": step_result.structured_output,
                    "tokens_used": step_result.tokens_used,
                    "cost": step_result.cost,
                }

                # Check if step was interrupted
                if step_result.was_interrupted:
                    await self._pause_pipeline(step_number)
                    return False

                # Check for clarification request
                if step_result.clarification_request:
                    return await self._handle_clarification(
                        step, step_number, step_result.clarification_request
                    )
            else:
                # Fallback to stateless execution (legacy behavior)
                result = await agent.execute(
                    context=AgentContext(**context),
                    on_token=lambda token: self._stream_token(step_number, token),
                    on_tool_call=lambda tool, args, tool_use_id, sn=step_number, sid=step["id"]: self._log_tool_call(sid, sn, tool, args, tool_use_id),
                )

                # Check for clarification request in stateless result
                if result.get("clarification_request"):
                    return await self._handle_clarification(
                        step, step_number, result["clarification_request"]
                    )

            # Save output with chronological events
            output_id = generate_id()
            events = self._step_events.get(step_number, [])
            content_json_data = {
                "events": events,  # Chronological events for UI display
                "structured_output": result.get("structured_output"),
            }
            await db.execute("""
                INSERT INTO step_outputs (id, step_id, output_type, content, content_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                output_id,
                step["id"],
                config["output_type"],
                result.get("content", ""),
                json_dumps(content_json_data),
                now,
            ))
            # Clear tracked events
            self._step_events.pop(step_number, None)

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
                "next_step": step_number + 1 if step_number < MAX_STEPS else None,
                "tokens_used": result.get("tokens_used", 0),
                "cost": result.get("cost", 0),
                "output": result.get("content", "")[:5000],  # Truncate for websocket
            })

            # After step 1 (Context Agent), set up worktrees if enabled
            if step_number == 1 and settings.worktree_enabled:
                worktree_ready = await self._setup_worktrees_after_context(result)
                if not worktree_ready:
                    # Pipeline is waiting for user input
                    return False

            # After step 7 (PR Agent), broadcast waiting_for_review if there's a next step
            if step_number == 7 and step_number < MAX_STEPS:
                # Get PR info if it was created
                pr = await db.fetchone("""
                    SELECT pr_number, pr_url FROM pull_requests WHERE pipeline_id = ?
                """, (self.pipeline_id,))
                if pr:
                    await db.execute("""
                        UPDATE pipelines SET status = 'waiting_for_review' WHERE id = ?
                    """, (self.pipeline_id,))
                    await db.commit()
                    await broadcast_message({
                        "type": "waiting_for_review",
                        "pipeline_id": self.pipeline_id,
                        "pr_number": pr["pr_number"],
                        "pr_url": pr["pr_url"],
                    })
                    # Return False to stop automatic progression - wait for review webhook
                    return False

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

        # Determine codebase path - use worktree if available
        codebase_path = settings.codebase_path
        is_worktree = False

        if settings.worktree_enabled and self._worktree_paths:
            # Use first worktree path as primary (for single-repo tickets)
            # For multi-repo, agents will need to handle multiple paths
            first_repo = list(self._worktree_paths.keys())[0]
            codebase_path = self._worktree_paths[first_repo]
            is_worktree = True

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
            "codebase_path": codebase_path,
            "sandbox_branch": f"{settings.sandbox_branch_prefix}{pipeline['ticket_key']}",
            "is_worktree": is_worktree,
            "worktree_paths": self._worktree_paths if is_worktree else {},
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

    async def _create_new_session(self, pipeline: dict) -> PipelineSession:
        """Create a new PipelineSession for a fresh pipeline.

        This initializes a persistent ClaudeSDKClient session that will
        maintain context across all pipeline steps.
        """
        db = await self.get_db()

        ticket = await db.fetchone(
            "SELECT * FROM tickets WHERE key = ?",
            (pipeline["ticket_key"],)
        )

        # Determine codebase path - use worktree if available
        codebase_path = settings.codebase_path
        if settings.worktree_enabled and self._worktree_paths:
            first_repo = list(self._worktree_paths.keys())[0]
            codebase_path = self._worktree_paths[first_repo]

        ticket_context = {
            "key": ticket["key"],
            "summary": ticket["summary"],
            "description": ticket["description"],
        }

        session = PipelineSession(
            pipeline_id=self.pipeline_id,
            codebase_path=codebase_path,
            ticket_context=ticket_context,
        )

        session_id = await session.start()
        logger.info(f"Created new PipelineSession {session_id} for pipeline {self.pipeline_id}")

        # Save session to database
        await session_store.save_session(
            pipeline_id=self.pipeline_id,
            session_id=session_id,
            cwd=codebase_path,
            ticket_context=ticket_context,
        )

        return session

    async def _restore_session(self, pipeline: dict) -> PipelineSession:
        """Restore an existing session for a resumed pipeline.

        Since we can't truly restore ClaudeSDKClient's internal state,
        this creates a new session and injects a summary of previous
        work to provide context continuity.
        """
        db = await self.get_db()

        # Try to get existing session info
        existing_session = await session_store.get_session(self.pipeline_id)

        ticket = await db.fetchone(
            "SELECT * FROM tickets WHERE key = ?",
            (pipeline["ticket_key"],)
        )

        # Determine codebase path
        codebase_path = settings.codebase_path
        if settings.worktree_enabled and self._worktree_paths:
            first_repo = list(self._worktree_paths.keys())[0]
            codebase_path = self._worktree_paths[first_repo]

        ticket_context = {
            "key": ticket["key"],
            "summary": ticket["summary"],
            "description": ticket["description"],
        }

        # Generate summary of previous work for context injection
        conversation_summary = None
        if existing_session:
            conversation_summary = existing_session.get('conversation_summary')

        if not conversation_summary:
            # Generate summary from step outputs
            conversation_summary = await session_store.generate_conversation_summary(
                self.pipeline_id
            )

        # Create restored session with context
        session = await PipelineSession.restore(
            session_id=existing_session.get('claude_session_id', '') if existing_session else '',
            pipeline_id=self.pipeline_id,
            codebase_path=codebase_path,
            ticket_context=ticket_context,
            conversation_summary=conversation_summary,
        )

        logger.info(f"Restored PipelineSession for pipeline {self.pipeline_id}")

        # Update session in database
        if existing_session:
            await session_store.update_session_progress(
                self.pipeline_id,
                pipeline["current_step"] - 1,
            )
        else:
            await session_store.save_session(
                pipeline_id=self.pipeline_id,
                session_id=session.session_id or '',
                cwd=codebase_path,
                ticket_context=ticket_context,
                last_step_completed=pipeline["current_step"] - 1,
            )

        return session

    async def _stream_token(self, step_number: int, token: str):
        """Stream token to clients and track for chronological storage."""
        # Track event for saving later
        if step_number not in self._step_events:
            self._step_events[step_number] = []

        events = self._step_events[step_number]
        now = datetime.utcnow().isoformat()
        # Merge consecutive text events
        if events and events[-1]["type"] == "text":
            events[-1]["content"] += token
            events[-1]["timestamp"] = now  # Update to latest
        else:
            events.append({"type": "text", "content": token, "timestamp": now})

        await broadcast_message({
            "type": "token",
            "pipeline_id": self.pipeline_id,
            "step": step_number,
            "token": token,
        })

    async def _log_tool_call(self, step_id: str, step_number: int, tool: str, args: dict, tool_use_id: str = None):
        """Log tool call to database and track for chronological storage."""
        db = await self.get_db()
        now = datetime.utcnow().isoformat()

        # Track event for saving later (chronological order)
        if step_number not in self._step_events:
            self._step_events[step_number] = []
        self._step_events[step_number].append({
            "type": "tool_call",
            "tool": tool,
            "tool_use_id": tool_use_id,
            "arguments": args,
            "timestamp": now,
        })

        tool_call_id = generate_id()
        await db.execute("""
            INSERT INTO tool_calls (id, step_id, tool_name, tool_use_id, arguments, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (tool_call_id, step_id, tool, tool_use_id, json_dumps(args), datetime.utcnow().isoformat()))
        await db.commit()

        await broadcast_message({
            "type": "tool_call_started",
            "pipeline_id": self.pipeline_id,
            "step": step_number,
            "tool": tool,
            "tool_use_id": tool_use_id,
            "arguments": args,
        })

    async def _log_subagent_tool_call(
        self,
        step_id: str,
        step_number: int,
        parent_tool_use_id: str,
        tool_name: str,
        tool_use_id: str,
        arguments: dict,
    ):
        """Log subagent tool call and broadcast in real-time."""
        db = await self.get_db()
        now = datetime.utcnow().isoformat()

        # Save to database
        subagent_tc_id = generate_id()
        await db.execute("""
            INSERT INTO subagent_tool_calls
            (id, pipeline_id, step_id, step_number, parent_tool_use_id, tool_use_id, tool_name, arguments, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            subagent_tc_id,
            self.pipeline_id,
            step_id,
            step_number,
            parent_tool_use_id,
            tool_use_id,
            tool_name,
            json_dumps(arguments),
            now,
        ))
        await db.commit()

        # Broadcast REAL-TIME to frontend
        await broadcast_message({
            "type": "subagent_tool_call",
            "pipeline_id": self.pipeline_id,
            "step": step_number,
            "parent_tool_use_id": parent_tool_use_id,
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "timestamp": now,
        })

    async def _stream_subagent_text(
        self,
        step_number: int,
        parent_tool_use_id: str,
        text: str,
    ):
        """Stream subagent text content in real-time."""
        now = datetime.utcnow().isoformat()

        # Broadcast REAL-TIME to frontend
        await broadcast_message({
            "type": "subagent_text",
            "pipeline_id": self.pipeline_id,
            "step": step_number,
            "parent_tool_use_id": parent_tool_use_id,
            "text": text,
            "timestamp": now,
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
        """Pause the pipeline and save session state."""
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

        # Generate and save conversation summary for later restoration
        if self._session:
            conversation_summary = await session_store.generate_conversation_summary(
                self.pipeline_id
            )
            await session_store.pause_session(
                self.pipeline_id,
                conversation_summary=conversation_summary,
            )

        await broadcast_message({
            "type": "pipeline_paused",
            "pipeline_id": self.pipeline_id,
            "step": current_step,
        })

    async def _handle_clarification(
        self,
        step: dict,
        step_number: int,
        clarification_request: dict
    ) -> bool:
        """Handle a clarification request from an agent.

        Creates a clarification record and pauses the pipeline waiting for user input.
        Returns False to stop pipeline execution.
        """
        db = await self.get_db()
        now = datetime.utcnow().isoformat()

        # Create clarification record
        clarification_id = generate_id()
        await db.execute("""
            INSERT INTO clarifications
            (id, step_id, pipeline_id, question, options, context, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (
            clarification_id,
            step["id"],
            self.pipeline_id,
            clarification_request.get("question", ""),
            json_dumps(clarification_request.get("options", [])),
            clarification_request.get("context", ""),
            now,
        ))

        # Update step to waiting state
        await db.execute("""
            UPDATE pipeline_steps
            SET status = 'waiting', waiting_for = 'clarification'
            WHERE id = ?
        """, (step["id"],))

        # Update pipeline status
        await db.execute("""
            UPDATE pipelines
            SET status = 'needs_user_input'
            WHERE id = ?
        """, (self.pipeline_id,))

        await db.commit()

        # Get ticket key for the broadcast
        pipeline = await db.fetchone(
            "SELECT ticket_key FROM pipelines WHERE id = ?",
            (self.pipeline_id,)
        )

        # Broadcast clarification request to clients
        await broadcast_message({
            "type": "clarification_requested",
            "pipeline_id": self.pipeline_id,
            "ticket_key": pipeline["ticket_key"],
            "step": step_number,
            "clarification_id": clarification_id,
            "question": clarification_request.get("question", ""),
            "options": clarification_request.get("options", []),
            "context": clarification_request.get("context", ""),
        })

        logger.info(
            f"Pipeline {self.pipeline_id} paused for clarification at step {step_number}: "
            f"{clarification_request.get('question', '')}"
        )

        return False  # Stop pipeline execution

    async def _complete_pipeline(self):
        """Mark pipeline as complete and close session."""
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

        # Mark session as completed
        await session_store.complete_session(self.pipeline_id)

        await broadcast_message({
            "type": "pipeline_completed",
            "pipeline_id": self.pipeline_id,
            "total_tokens": pipeline["total_tokens"],
            "total_cost": pipeline["total_cost"],
        })

    async def _load_existing_worktree_session(self):
        """Load existing worktree session for this pipeline if available."""
        from .worktree_manager import WorktreeManager

        if not self._worktree_manager:
            self._worktree_manager = WorktreeManager()

        session = await self._worktree_manager.get_session_by_pipeline(self.pipeline_id)

        if session and session.status.value == 'ready':
            self._worktree_session = session
            # Populate worktree paths from the session
            for repo in session.repos:
                self._worktree_paths[repo.repo_name] = repo.worktree_path
            logger.info(f"Loaded existing worktree session for pipeline {self.pipeline_id}: {self._worktree_paths}")
        else:
            logger.info(f"No ready worktree session found for pipeline {self.pipeline_id}, using main codebase")

    async def _setup_worktrees_after_context(self, context_result: dict) -> bool:
        """
        Set up worktrees after Context Agent completes.

        Returns True if worktrees are ready, False if waiting for user input.
        """
        from .worktree_manager import WorktreeManager, AffectedRepo

        db = await self.get_db()
        pipeline = await db.fetchone(
            "SELECT ticket_key FROM pipelines WHERE id = ?",
            (self.pipeline_id,)
        )

        # Initialize worktree manager
        self._worktree_manager = WorktreeManager()

        # Extract affected repos from context output
        structured = context_result.get("structured_output", {})
        affected_repos_data = structured.get("affected_repos", []) if structured else []

        # If no repos detected, try to detect all repos in base path
        if not affected_repos_data:
            all_repos = await self._worktree_manager.detect_repos()
            affected_repos_data = [{"name": r, "reason": "Auto-detected"} for r in all_repos]

        if not affected_repos_data:
            # No repos found, continue without worktrees
            return True

        # Convert to AffectedRepo objects
        affected_repos = [
            AffectedRepo(name=r.get("name", r), reason=r.get("reason", ""))
            for r in affected_repos_data
        ]

        try:
            # Create worktree session
            session = await self._worktree_manager.create_session(
                pipeline_id=self.pipeline_id,
                ticket_key=pipeline["ticket_key"],
                affected_repos=affected_repos
            )
            self._worktree_session = session

            # Run setup
            setup_result = await self._worktree_manager.run_setup(session.id)

            if setup_result.needs_user_input:
                # Update pipeline status
                await db.execute("""
                    UPDATE pipelines SET status = 'needs_user_input' WHERE id = ?
                """, (self.pipeline_id,))
                await db.commit()

                # Broadcast needs input
                await broadcast_message({
                    "type": "pipeline_needs_input",
                    "pipeline_id": self.pipeline_id,
                    "input_type": "setup_commands",
                    "repos": setup_result.repos_needing_input,
                })

                return False

            # Store worktree paths for use in agent context
            for repo in session.repos:
                self._worktree_paths[repo.repo_name] = repo.worktree_path

            return True

        except Exception as e:
            # Log error but continue without worktrees
            await broadcast_message({
                "type": "worktree_error",
                "pipeline_id": self.pipeline_id,
                "error": str(e),
            })
            return True  # Continue without worktrees

    async def resume_after_user_input(self):
        """
        Resume pipeline after user provides setup commands.

        Called when user provides input for worktree setup.
        The setup commands have already been processed by WorktreeManager.provide_user_input()
        before this method is called.
        """
        logger.info(f"[RESUME] resume_after_user_input called for pipeline {self.pipeline_id}")

        if not self._worktree_manager:
            from .worktree_manager import WorktreeManager
            self._worktree_manager = WorktreeManager()

        # Reload session to get updated paths after setup
        logger.info(f"[RESUME] Loading worktree session for pipeline {self.pipeline_id}")
        self._worktree_session = await self._worktree_manager.get_session_by_pipeline(
            self.pipeline_id
        )

        if not self._worktree_session:
            logger.error(f"[RESUME] No worktree session found for pipeline {self.pipeline_id}")
            return

        logger.info(f"[RESUME] Session status: {self._worktree_session.status}, repos: {len(self._worktree_session.repos)}")

        # Check if session is ready (status is an enum but inherits from str)
        if self._worktree_session.status.value != 'ready':
            logger.error(f"[RESUME] Worktree session not ready: {self._worktree_session.status}")
            # Log repo statuses
            for repo in self._worktree_session.repos:
                logger.error(f"[RESUME]   Repo {repo.repo_name}: status={repo.status}, path={repo.worktree_path}")
            return

        # Store worktree paths
        for repo in self._worktree_session.repos:
            self._worktree_paths[repo.repo_name] = repo.worktree_path
            logger.info(f"[RESUME] Worktree path: {repo.repo_name} -> {repo.worktree_path}")

        # Update pipeline status and resume
        db = await self.get_db()
        await db.execute("""
            UPDATE pipelines SET status = 'running' WHERE id = ?
        """, (self.pipeline_id,))
        await db.commit()
        logger.info(f"[RESUME] Updated pipeline status to 'running'")

        # Broadcast that pipeline is resuming
        await broadcast_message({
            "type": "pipeline_resumed",
            "pipeline_id": self.pipeline_id,
            "step": 2,  # Resume from step 2
        })
        logger.info(f"[RESUME] Broadcasted pipeline_resumed, calling run()")

        # Continue pipeline execution
        await self.run()

    async def run_review_step(self, comments: list[dict]):
        """Run the review agent for PR comments using PipelineSession if available."""
        db = await self.get_db()
        step_number = 8

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

        # Try to restore session for review step
        if not self._session:
            try:
                pipeline = await db.fetchone(
                    "SELECT * FROM pipelines WHERE id = ?",
                    (self.pipeline_id,)
                )
                self._session = await self._restore_session(pipeline)
            except Exception as e:
                logger.warning(f"Could not restore session for review step: {e}")
                self._session = None

        # Build context with comments
        context = await self._build_agent_context(step_number)
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
        config = get_step_config(step_number)
        agent = create_agent(
            agent_type=config["agent_type"],
            model=config["model"],
            max_tokens=config["max_tokens"],
            tools=config["tools"],
        )

        try:
            # Use PipelineSession if available for context persistence
            if self._session:
                step_result = await self._session.execute_step(
                    step_number=step_number,
                    agent=agent,
                    context=AgentContext(**context),
                    on_token=lambda token: self._stream_token(step_number, token),
                    on_tool_call=lambda tool, args, tool_use_id, sn=step_number, sid=step["id"]: self._log_tool_call(sid, sn, tool, args, tool_use_id),
                    on_subagent_tool_call=lambda parent_id, tool, tool_id, args, sn=step_number, sid=step["id"]: self._log_subagent_tool_call(sid, sn, parent_id, tool, tool_id, args),
                )
                result = {
                    "content": step_result.content,
                    "structured_output": step_result.structured_output,
                    "tokens_used": step_result.tokens_used,
                    "cost": step_result.cost,
                }
            else:
                # Fallback to stateless execution
                result = await agent.execute(
                    context=AgentContext(**context),
                    on_token=lambda token: self._stream_token(step_number, token),
                    on_tool_call=lambda tool, args, tool_use_id, sn=step_number, sid=step["id"]: self._log_tool_call(sid, sn, tool, args, tool_use_id),
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

            # Update session progress after successful review
            if self._session:
                await session_store.update_session_progress(
                    self.pipeline_id,
                    step_number,
                )

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
                "step": step_number,
                "error": str(e),
            })

        finally:
            # Clean up session after review step
            if self._session:
                await self._session.close()
                self._session = None
