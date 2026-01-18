"""
Git Worktree Manager Service

Manages git worktree lifecycle for pipeline isolation:
- Creating worktrees for each affected repository
- Running setup commands (detected or user-provided)
- Cleaning up worktrees after PR merge
"""

import asyncio
import shutil
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

from config import settings
from db import get_db, generate_id, json_dumps, json_loads
from websocket.manager import broadcast_message
from .setup_detector import SetupDetector, SetupResult, Confidence


class WorktreeSessionStatus(str, Enum):
    PENDING = "pending"
    CREATING = "creating"
    READY = "ready"
    NEEDS_USER_INPUT = "needs_user_input"
    FAILED = "failed"
    CLEANED = "cleaned"


class WorktreeRepoStatus(str, Enum):
    PENDING = "pending"
    CREATING = "creating"
    SETUP = "setup"
    READY = "ready"
    FAILED = "failed"


@dataclass
class AffectedRepo:
    """A repository affected by a ticket."""
    name: str
    reason: str = ""


@dataclass
class WorktreeRepoInfo:
    """Information about a single repo worktree."""
    id: str
    session_id: str
    repo_name: str
    repo_path: str
    worktree_path: str
    branch_name: str
    status: WorktreeRepoStatus
    setup_commands: Optional[List[str]] = None
    setup_output: Optional[str] = None
    pr_url: Optional[str] = None
    pr_merged: bool = False


@dataclass
class WorktreeSession:
    """A worktree session containing one or more repo worktrees."""
    id: str
    pipeline_id: str
    ticket_key: str
    status: WorktreeSessionStatus
    base_path: str
    repos: List[WorktreeRepoInfo] = field(default_factory=list)
    error_message: Optional[str] = None
    user_input_request: Optional[Dict] = None


@dataclass
class SetupExecutionResult:
    """Result of running setup for all repos in a session."""
    success: bool
    needs_user_input: bool
    repos_needing_input: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


class WorktreeManager:
    """Manages git worktree lifecycle for pipeline isolation."""

    def __init__(self):
        self.base_path = Path(settings.worktree_base_path)
        self.storage_path = Path(settings.worktree_storage_path)
        self.source_branch = settings.worktree_source_branch
        self.timeout = settings.worktree_setup_timeout_seconds
        self.setup_detector = SetupDetector()

    async def create_session(
        self,
        pipeline_id: str,
        ticket_key: str,
        affected_repos: List[AffectedRepo]
    ) -> WorktreeSession:
        """
        Create a new worktree session for a pipeline.

        Args:
            pipeline_id: The pipeline ID
            ticket_key: JIRA ticket key (e.g., PROJ-123)
            affected_repos: List of repos that need worktrees

        Returns:
            WorktreeSession with created worktrees
        """
        db = await get_db()
        session_id = generate_id()
        now = datetime.utcnow().isoformat()

        # Create session directory
        session_base_path = self.storage_path / ticket_key
        session_base_path.mkdir(parents=True, exist_ok=True)

        # Insert session record
        await db.execute("""
            INSERT INTO worktree_sessions
            (id, pipeline_id, ticket_key, status, base_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            session_id, pipeline_id, ticket_key,
            WorktreeSessionStatus.CREATING.value,
            str(session_base_path), now
        ))
        await db.commit()

        # Broadcast session creation starting
        await broadcast_message({
            "type": "worktree_session_creating",
            "pipeline_id": pipeline_id,
            "ticket_key": ticket_key,
            "repos": [r.name for r in affected_repos],
        })

        session = WorktreeSession(
            id=session_id,
            pipeline_id=pipeline_id,
            ticket_key=ticket_key,
            status=WorktreeSessionStatus.CREATING,
            base_path=str(session_base_path),
        )

        # Create worktree for each repo
        for repo in affected_repos:
            try:
                repo_info = await self._create_repo_worktree(
                    session_id=session_id,
                    ticket_key=ticket_key,
                    repo_name=repo.name,
                    session_base_path=session_base_path
                )
                session.repos.append(repo_info)
            except Exception as e:
                # Log error but continue with other repos
                await self._log_error(session_id, f"Failed to create worktree for {repo.name}: {e}")

        return session

    async def _create_repo_worktree(
        self,
        session_id: str,
        ticket_key: str,
        repo_name: str,
        session_base_path: Path
    ) -> WorktreeRepoInfo:
        """Create a single repo worktree."""
        db = await get_db()
        repo_id = generate_id()
        now = datetime.utcnow().isoformat()

        repo_path = self.base_path / repo_name
        worktree_path = session_base_path / repo_name
        branch_name = f"{settings.sandbox_branch_prefix}{ticket_key}"

        # Insert repo record
        await db.execute("""
            INSERT INTO worktree_repos
            (id, session_id, repo_name, repo_path, worktree_path, branch_name, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            repo_id, session_id, repo_name,
            str(repo_path), str(worktree_path), branch_name,
            WorktreeRepoStatus.CREATING.value, now
        ))
        await db.commit()

        try:
            # Fetch latest from remote
            await self._run_command(
                f"git fetch origin {self.source_branch}",
                cwd=repo_path
            )

            # Check if branch already exists
            result = await self._run_command(
                f"git branch --list {branch_name}",
                cwd=repo_path
            )

            if result[1].strip():
                # Branch exists, delete it first (from previous run)
                await self._run_command(
                    f"git branch -D {branch_name}",
                    cwd=repo_path
                )

            # Check if worktree path already exists
            if worktree_path.exists():
                # Remove existing worktree
                await self._run_command(
                    f"git worktree remove {worktree_path} --force",
                    cwd=repo_path
                )
                if worktree_path.exists():
                    shutil.rmtree(worktree_path)

            # Create worktree with new branch
            await self._run_command(
                f"git worktree add -b {branch_name} {worktree_path} origin/{self.source_branch}",
                cwd=repo_path
            )

            # Update status
            await db.execute("""
                UPDATE worktree_repos SET status = ? WHERE id = ?
            """, (WorktreeRepoStatus.PENDING.value, repo_id))
            await db.commit()

            # Broadcast repo created
            await broadcast_message({
                "type": "worktree_repo_created",
                "pipeline_id": (await self._get_pipeline_id(session_id)),
                "repo_name": repo_name,
                "worktree_path": str(worktree_path),
                "branch": branch_name,
            })

            return WorktreeRepoInfo(
                id=repo_id,
                session_id=session_id,
                repo_name=repo_name,
                repo_path=str(repo_path),
                worktree_path=str(worktree_path),
                branch_name=branch_name,
                status=WorktreeRepoStatus.PENDING,
            )

        except Exception as e:
            await db.execute("""
                UPDATE worktree_repos SET status = ?, setup_output = ? WHERE id = ?
            """, (WorktreeRepoStatus.FAILED.value, str(e), repo_id))
            await db.commit()
            raise

    async def run_setup(self, session_id: str) -> SetupExecutionResult:
        """
        Run setup detection and execution for all repos in a session.

        Returns:
            SetupExecutionResult indicating success or if user input is needed
        """
        db = await get_db()

        repos = await db.fetchall("""
            SELECT * FROM worktree_repos WHERE session_id = ?
        """, (session_id,))

        repos_needing_input = []
        all_success = True

        for repo in repos:
            repo_path = Path(repo["worktree_path"])

            # Update status to setup
            await db.execute("""
                UPDATE worktree_repos SET status = ? WHERE id = ?
            """, (WorktreeRepoStatus.SETUP.value, repo["id"]))
            await db.commit()

            # Broadcast setup started
            pipeline_id = await self._get_pipeline_id(session_id)
            await broadcast_message({
                "type": "worktree_setup_started",
                "pipeline_id": pipeline_id,
                "repo_name": repo["repo_name"],
            })

            # Detect setup commands
            setup_result = await self.setup_detector.detect_setup(repo_path)

            if setup_result.needs_user_input:
                repos_needing_input.append({
                    "name": repo["repo_name"],
                    "files_checked": setup_result.files_checked,
                    "reasoning": setup_result.reasoning,
                })
                all_success = False
                continue

            # Run setup commands
            commands = setup_result.commands or self.setup_detector.get_default_commands(repo_path)
            success = await self._run_setup_commands(repo["id"], repo_path, commands)

            if not success:
                all_success = False

        if repos_needing_input:
            # Update session status
            await db.execute("""
                UPDATE worktree_sessions
                SET status = ?, user_input_request = ?
                WHERE id = ?
            """, (
                WorktreeSessionStatus.NEEDS_USER_INPUT.value,
                json_dumps({"repos": repos_needing_input}),
                session_id
            ))
            await db.commit()

            return SetupExecutionResult(
                success=False,
                needs_user_input=True,
                repos_needing_input=repos_needing_input,
            )

        if all_success:
            now = datetime.utcnow().isoformat()
            await db.execute("""
                UPDATE worktree_sessions SET status = ?, ready_at = ? WHERE id = ?
            """, (WorktreeSessionStatus.READY.value, now, session_id))
            await db.commit()

            # Broadcast session ready
            await broadcast_message({
                "type": "worktree_session_ready",
                "pipeline_id": await self._get_pipeline_id(session_id),
                "repos": [{"name": r["repo_name"], "path": r["worktree_path"]} for r in repos],
            })

        return SetupExecutionResult(
            success=all_success,
            needs_user_input=False,
        )

    async def _run_setup_commands(
        self,
        repo_id: str,
        repo_path: Path,
        commands: List[str]
    ) -> bool:
        """Run setup commands for a repo worktree."""
        db = await get_db()
        main_repo_path = Path((await db.fetchone(
            "SELECT repo_path FROM worktree_repos WHERE id = ?", (repo_id,)
        ))["repo_path"])

        output_lines = []

        try:
            # Copy .env from main repo if exists
            env_source = main_repo_path / ".env"
            env_dest = repo_path / ".env"
            if env_source.exists() and not env_dest.exists():
                shutil.copy2(env_source, env_dest)
                output_lines.append(f"Copied .env from {env_source}")

            # Run each command
            for cmd in commands:
                output_lines.append(f"\n$ {cmd}")
                success, output = await self._run_command(
                    cmd,
                    cwd=repo_path,
                    timeout=self.timeout
                )
                output_lines.append(output)

                if not success:
                    raise RuntimeError(f"Command failed: {cmd}\n{output}")

            # Update repo status
            now = datetime.utcnow().isoformat()
            await db.execute("""
                UPDATE worktree_repos
                SET status = ?, setup_commands = ?, setup_output = ?, ready_at = ?
                WHERE id = ?
            """, (
                WorktreeRepoStatus.READY.value,
                json_dumps(commands),
                "\n".join(output_lines),
                now,
                repo_id
            ))
            await db.commit()

            return True

        except Exception as e:
            await db.execute("""
                UPDATE worktree_repos
                SET status = ?, setup_output = ?
                WHERE id = ?
            """, (
                WorktreeRepoStatus.FAILED.value,
                "\n".join(output_lines) + f"\n\nError: {e}",
                repo_id
            ))
            await db.commit()
            return False

    async def provide_user_input(
        self,
        session_id: str,
        setup_commands: Dict[str, List[str]]
    ) -> SetupExecutionResult:
        """
        Apply user-provided setup commands and run setup.

        Args:
            session_id: The session ID
            setup_commands: Dict mapping repo names to command lists

        Returns:
            SetupExecutionResult
        """
        db = await get_db()

        # Update session with user response
        await db.execute("""
            UPDATE worktree_sessions
            SET status = ?, user_input_response = ?
            WHERE id = ?
        """, (
            WorktreeSessionStatus.CREATING.value,
            json_dumps(setup_commands),
            session_id
        ))
        await db.commit()

        # Run setup for each repo with provided commands
        repos = await db.fetchall("""
            SELECT * FROM worktree_repos WHERE session_id = ?
        """, (session_id,))

        all_success = True

        for repo in repos:
            repo_name = repo["repo_name"]
            commands = setup_commands.get(repo_name, [])

            if not commands:
                # Use default detection for repos without user input
                repo_path = Path(repo["worktree_path"])
                commands = self.setup_detector.get_default_commands(repo_path)

            success = await self._run_setup_commands(
                repo["id"],
                Path(repo["worktree_path"]),
                commands
            )

            if not success:
                all_success = False

        if all_success:
            now = datetime.utcnow().isoformat()
            await db.execute("""
                UPDATE worktree_sessions SET status = ?, ready_at = ? WHERE id = ?
            """, (WorktreeSessionStatus.READY.value, now, session_id))
            await db.commit()

            # Broadcast session ready
            await broadcast_message({
                "type": "worktree_session_ready",
                "pipeline_id": await self._get_pipeline_id(session_id),
                "repos": [{"name": r["repo_name"], "path": r["worktree_path"]} for r in repos],
            })

        return SetupExecutionResult(success=all_success, needs_user_input=False)

    async def cleanup_session(self, session_id: str, force: bool = False) -> bool:
        """
        Clean up a worktree session.

        Args:
            session_id: The session ID
            force: If True, cleanup regardless of PR merge status

        Returns:
            True if cleanup was successful
        """
        db = await get_db()

        session = await db.fetchone("""
            SELECT * FROM worktree_sessions WHERE id = ?
        """, (session_id,))

        if not session:
            return False

        repos = await db.fetchall("""
            SELECT * FROM worktree_repos WHERE session_id = ?
        """, (session_id,))

        # Check if all PRs are merged (unless force)
        if not force and settings.worktree_cleanup_on_merge:
            for repo in repos:
                if repo["pr_url"] and not repo["pr_merged"]:
                    return False  # PR not merged yet

        # Clean up each repo worktree
        for repo in repos:
            try:
                repo_path = Path(repo["repo_path"])
                worktree_path = Path(repo["worktree_path"])

                # Remove worktree
                if worktree_path.exists():
                    await self._run_command(
                        f"git worktree remove {worktree_path} --force",
                        cwd=repo_path
                    )

                # Delete branch
                await self._run_command(
                    f"git branch -D {repo['branch_name']}",
                    cwd=repo_path
                )

                # Clean up directory if still exists
                if worktree_path.exists():
                    shutil.rmtree(worktree_path)

            except Exception as e:
                await self._log_error(session_id, f"Cleanup failed for {repo['repo_name']}: {e}")

        # Remove session directory
        session_path = Path(session["base_path"])
        if session_path.exists():
            try:
                shutil.rmtree(session_path)
            except Exception:
                pass

        # Update session status
        now = datetime.utcnow().isoformat()
        await db.execute("""
            UPDATE worktree_sessions SET status = ?, cleaned_at = ? WHERE id = ?
        """, (WorktreeSessionStatus.CLEANED.value, now, session_id))
        await db.commit()

        # Broadcast cleanup
        await broadcast_message({
            "type": "worktree_session_cleaned",
            "pipeline_id": session["pipeline_id"],
        })

        return True

    async def get_session_by_pipeline(self, pipeline_id: str) -> Optional[WorktreeSession]:
        """Get worktree session for a pipeline."""
        db = await get_db()

        session = await db.fetchone("""
            SELECT * FROM worktree_sessions WHERE pipeline_id = ?
        """, (pipeline_id,))

        if not session:
            return None

        repos = await db.fetchall("""
            SELECT * FROM worktree_repos WHERE session_id = ?
        """, (session["id"],))

        return WorktreeSession(
            id=session["id"],
            pipeline_id=session["pipeline_id"],
            ticket_key=session["ticket_key"],
            status=WorktreeSessionStatus(session["status"]),
            base_path=session["base_path"],
            repos=[
                WorktreeRepoInfo(
                    id=r["id"],
                    session_id=r["session_id"],
                    repo_name=r["repo_name"],
                    repo_path=r["repo_path"],
                    worktree_path=r["worktree_path"],
                    branch_name=r["branch_name"],
                    status=WorktreeRepoStatus(r["status"]),
                    setup_commands=json_loads(r["setup_commands"]) if r["setup_commands"] else None,
                    setup_output=r["setup_output"],
                    pr_url=r["pr_url"],
                    pr_merged=bool(r["pr_merged"]),
                )
                for r in repos
            ],
            error_message=session["error_message"],
            user_input_request=json_loads(session["user_input_request"]) if session["user_input_request"] else None,
        )

    async def mark_pr_merged(self, branch_name: str, pr_url: str) -> bool:
        """Mark a PR as merged for cleanup tracking."""
        db = await get_db()

        result = await db.execute("""
            UPDATE worktree_repos
            SET pr_merged = TRUE
            WHERE branch_name = ? OR pr_url = ?
        """, (branch_name, pr_url))
        await db.commit()

        return result.rowcount > 0

    async def detect_repos(self) -> List[str]:
        """Detect all git repositories in the base path."""
        repos = []
        if self.base_path.exists():
            for item in self.base_path.iterdir():
                if item.is_dir() and (item / ".git").exists():
                    repos.append(item.name)
        return sorted(repos)

    async def _run_command(
        self,
        cmd: str,
        cwd: Path,
        timeout: int = 120
    ) -> tuple[bool, str]:
        """Run a shell command asynchronously."""
        try:
            process = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
                output = stdout.decode("utf-8", errors="ignore")
                return (process.returncode == 0, output)

            except asyncio.TimeoutError:
                process.kill()
                return (False, f"Command timed out after {timeout}s")

        except Exception as e:
            return (False, str(e))

    async def _get_pipeline_id(self, session_id: str) -> str:
        """Get pipeline ID from session."""
        db = await get_db()
        session = await db.fetchone(
            "SELECT pipeline_id FROM worktree_sessions WHERE id = ?",
            (session_id,)
        )
        return session["pipeline_id"] if session else ""

    async def _log_error(self, session_id: str, error: str) -> None:
        """Log an error to the session."""
        db = await get_db()
        session = await db.fetchone(
            "SELECT error_message FROM worktree_sessions WHERE id = ?",
            (session_id,)
        )
        existing = session["error_message"] or "" if session else ""
        new_error = f"{existing}\n{error}".strip()

        await db.execute("""
            UPDATE worktree_sessions SET error_message = ? WHERE id = ?
        """, (new_error, session_id))
        await db.commit()
