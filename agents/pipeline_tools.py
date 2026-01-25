"""
Custom MCP Tools for Pipeline Operations

Provides specialized tools for the BiAgent pipeline system using Claude Agent SDK.
"""

from typing import Any
from datetime import datetime
import json
import os

from claude_agent_sdk import tool, create_sdk_mcp_server


@tool("get_ticket_details", "Get full details of a JIRA ticket", {
    "ticket_key": str
})
async def get_ticket_details(args: dict[str, Any]) -> dict[str, Any]:
    """Fetch ticket details from the database."""
    ticket_key = args.get("ticket_key")

    # In production, this would query the database
    # For now, return a placeholder
    return {
        "content": [{
            "type": "text",
            "text": f"Ticket {ticket_key} details retrieved. Use the ticket context provided in the prompt."
        }]
    }


@tool("update_pipeline_status", "Update the status of a pipeline step", {
    "pipeline_id": str,
    "step_number": int,
    "status": str,
    "message": str
})
async def update_pipeline_status(args: dict[str, Any]) -> dict[str, Any]:
    """Update pipeline step status."""
    pipeline_id = args.get("pipeline_id")
    step_number = args.get("step_number")
    status = args.get("status")
    message = args.get("message", "")

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "pipeline_id": pipeline_id,
        "step": step_number,
        "status": status,
        "message": message
    }

    print(f"[PIPELINE] {json.dumps(log_entry)}")

    return {
        "content": [{
            "type": "text",
            "text": f"Pipeline {pipeline_id} step {step_number} updated to {status}"
        }]
    }


@tool("log_agent_action", "Log an action taken by the agent for audit purposes", {
    "action_type": str,
    "description": str,
    "metadata": dict
})
async def log_agent_action(args: dict[str, Any]) -> dict[str, Any]:
    """Log agent actions for auditing."""
    action_type = args.get("action_type")
    description = args.get("description")
    metadata = args.get("metadata", {})

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "type": action_type,
        "description": description,
        "metadata": metadata
    }

    print(f"[AGENT_ACTION] {json.dumps(log_entry)}")

    return {
        "content": [{
            "type": "text",
            "text": f"Action logged: {action_type}"
        }]
    }


@tool("check_git_status", "Check the git status of the codebase", {
    "path": str
})
async def check_git_status(args: dict[str, Any]) -> dict[str, Any]:
    """Check git status."""
    import subprocess

    path = args.get("path", ".")

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            changes = result.stdout.strip().split("\n") if result.stdout.strip() else []
            return {
                "content": [{
                    "type": "text",
                    "text": f"Git status: {len(changes)} changed files\n{result.stdout}"
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Git error: {result.stderr}"
                }],
                "is_error": True
            }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": f"Error checking git status: {e}"
            }],
            "is_error": True
        }


@tool("create_sandbox_branch", "Create a sandbox branch for safe code changes", {
    "branch_name": str,
    "base_branch": str
})
async def create_sandbox_branch(args: dict[str, Any]) -> dict[str, Any]:
    """Create a sandbox branch."""
    import subprocess

    branch_name = args.get("branch_name")
    base_branch = args.get("base_branch", "main")

    try:
        # Fetch latest
        subprocess.run(["git", "fetch"], capture_output=True, timeout=60)

        # Create and checkout branch
        result = subprocess.run(
            ["git", "checkout", "-b", branch_name, f"origin/{base_branch}"],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Created and checked out branch: {branch_name}"
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Failed to create branch: {result.stderr}"
                }],
                "is_error": True
            }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": f"Error creating branch: {e}"
            }],
            "is_error": True
        }


@tool("run_tests", "Run the project's test suite", {
    "test_path": str,
    "test_pattern": str
})
async def run_tests(args: dict[str, Any]) -> dict[str, Any]:
    """Run tests and return results."""
    import subprocess

    test_path = args.get("test_path", ".")
    test_pattern = args.get("test_pattern", "")

    try:
        cmd = ["python", "-m", "pytest", test_path, "-v", "--tb=short"]
        if test_pattern:
            cmd.extend(["-k", test_pattern])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )

        return {
            "content": [{
                "type": "text",
                "text": f"Test Results (exit code {result.returncode}):\n{result.stdout}\n{result.stderr}"
            }]
        }
    except subprocess.TimeoutExpired:
        return {
            "content": [{
                "type": "text",
                "text": "Tests timed out after 5 minutes"
            }],
            "is_error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": f"Error running tests: {e}"
            }],
            "is_error": True
        }


@tool("analyze_code_complexity", "Analyze code complexity metrics", {
    "file_path": str
})
async def analyze_code_complexity(args: dict[str, Any]) -> dict[str, Any]:
    """Analyze code complexity."""
    file_path = args.get("file_path")

    if not os.path.exists(file_path):
        return {
            "content": [{
                "type": "text",
                "text": f"File not found: {file_path}"
            }],
            "is_error": True
        }

    try:
        with open(file_path, "r") as f:
            content = f.read()

        lines = content.split("\n")
        total_lines = len(lines)
        code_lines = len([l for l in lines if l.strip() and not l.strip().startswith("#")])
        comment_lines = len([l for l in lines if l.strip().startswith("#")])

        # Simple complexity heuristics
        indent_levels = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
        max_indent = max(indent_levels) if indent_levels else 0
        avg_indent = sum(indent_levels) / len(indent_levels) if indent_levels else 0

        analysis = {
            "file": file_path,
            "total_lines": total_lines,
            "code_lines": code_lines,
            "comment_lines": comment_lines,
            "max_indentation": max_indent,
            "avg_indentation": round(avg_indent, 2),
            "functions": content.count("def "),
            "classes": content.count("class "),
        }

        return {
            "content": [{
                "type": "text",
                "text": f"Code Analysis:\n{json.dumps(analysis, indent=2)}"
            }]
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": f"Error analyzing code: {e}"
            }],
            "is_error": True
        }


@tool("github_cli", "Execute GitHub CLI (gh) commands for PR operations", {
    "command": str,
    "args": list,
    "repo": str
})
async def github_cli(args: dict[str, Any]) -> dict[str, Any]:
    """
    Execute GitHub CLI commands.

    Supported commands:
    - pr view: View PR details
    - pr comment: Add a comment to a PR
    - pr review: Submit a PR review
    - pr diff: Get PR diff
    - pr checks: View PR check status

    Args:
        command: The gh subcommand (e.g., "pr view", "pr comment")
        args: List of arguments for the command
        repo: Repository in owner/repo format (optional)
    """
    import subprocess

    command = args.get("command", "")
    cmd_args = args.get("args", [])
    repo = args.get("repo", "")

    # Validate command is PR-related for safety
    allowed_commands = ["pr view", "pr comment", "pr review", "pr diff", "pr checks", "pr list"]
    if not any(command.startswith(allowed) for allowed in allowed_commands):
        return {
            "content": [{
                "type": "text",
                "text": f"Command not allowed. Allowed commands: {', '.join(allowed_commands)}"
            }],
            "is_error": True
        }

    try:
        # Build the gh command
        full_cmd = ["gh"] + command.split() + cmd_args
        if repo:
            full_cmd.extend(["--repo", repo])

        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "GH_PROMPT_DISABLED": "1"}
        )

        if result.returncode == 0:
            return {
                "content": [{
                    "type": "text",
                    "text": result.stdout or "Command completed successfully"
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": f"GitHub CLI error: {result.stderr or result.stdout}"
                }],
                "is_error": True
            }
    except subprocess.TimeoutExpired:
        return {
            "content": [{
                "type": "text",
                "text": "GitHub CLI command timed out"
            }],
            "is_error": True
        }
    except FileNotFoundError:
        return {
            "content": [{
                "type": "text",
                "text": "GitHub CLI (gh) not found. Please install it: https://cli.github.com/"
            }],
            "is_error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": f"Error executing GitHub CLI: {e}"
            }],
            "is_error": True
        }


@tool("reply_to_pr_comment", "Reply to a specific comment on a PR", {
    "pr_number": int,
    "comment_id": int,
    "body": str,
    "repo": str
})
async def reply_to_pr_comment(args: dict[str, Any]) -> dict[str, Any]:
    """
    Reply to a specific comment on a pull request.

    Args:
        pr_number: The PR number
        comment_id: The ID of the comment to reply to
        body: The reply text
        repo: Repository in owner/repo format
    """
    import subprocess

    pr_number = args.get("pr_number")
    comment_id = args.get("comment_id")
    body = args.get("body", "")
    repo = args.get("repo", "")

    if not all([pr_number, comment_id, body]):
        return {
            "content": [{
                "type": "text",
                "text": "Missing required arguments: pr_number, comment_id, and body are required"
            }],
            "is_error": True
        }

    try:
        # Use GitHub API via gh to reply to a comment
        cmd = [
            "gh", "api",
            f"repos/{repo}/pulls/{pr_number}/comments/{comment_id}/replies",
            "-X", "POST",
            "-f", f"body={body}"
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "GH_PROMPT_DISABLED": "1"}
        )

        if result.returncode == 0:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Successfully replied to comment {comment_id} on PR #{pr_number}"
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Failed to reply: {result.stderr or result.stdout}"
                }],
                "is_error": True
            }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": f"Error replying to comment: {e}"
            }],
            "is_error": True
        }


@tool("get_file_diff", "Get the diff for specific files in a PR or between commits", {
    "pr_number": int,
    "file_path": str,
    "repo": str,
    "base_ref": str,
    "head_ref": str
})
async def get_file_diff(args: dict[str, Any]) -> dict[str, Any]:
    """
    Get the diff for specific files.

    Can be used to get:
    1. Diff for a file in a PR (provide pr_number)
    2. Diff between two refs (provide base_ref and head_ref)

    Args:
        pr_number: The PR number (optional, for PR diffs)
        file_path: Path to the file to get diff for
        repo: Repository in owner/repo format
        base_ref: Base ref for comparison (optional)
        head_ref: Head ref for comparison (optional)
    """
    import subprocess

    pr_number = args.get("pr_number")
    file_path = args.get("file_path", "")
    repo = args.get("repo", "")
    base_ref = args.get("base_ref", "")
    head_ref = args.get("head_ref", "")

    try:
        if pr_number:
            # Get diff from PR
            cmd = ["gh", "pr", "diff", str(pr_number)]
            if repo:
                cmd.extend(["--repo", repo])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "GH_PROMPT_DISABLED": "1"}
            )

            if result.returncode != 0:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Failed to get PR diff: {result.stderr}"
                    }],
                    "is_error": True
                }

            diff_output = result.stdout

            # Filter to specific file if provided
            if file_path:
                # Parse the diff to extract only the relevant file
                lines = diff_output.split("\n")
                filtered_lines = []
                in_file = False

                for line in lines:
                    if line.startswith("diff --git"):
                        in_file = file_path in line
                    if in_file:
                        filtered_lines.append(line)

                diff_output = "\n".join(filtered_lines) if filtered_lines else f"No diff found for {file_path}"

            return {
                "content": [{
                    "type": "text",
                    "text": diff_output
                }]
            }

        elif base_ref and head_ref:
            # Get diff between refs using git
            cmd = ["git", "diff", f"{base_ref}...{head_ref}"]
            if file_path:
                cmd.append("--")
                cmd.append(file_path)

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                return {
                    "content": [{
                        "type": "text",
                        "text": result.stdout or "No differences found"
                    }]
                }
            else:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Failed to get diff: {result.stderr}"
                    }],
                    "is_error": True
                }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": "Must provide either pr_number or both base_ref and head_ref"
                }],
                "is_error": True
            }

    except subprocess.TimeoutExpired:
        return {
            "content": [{
                "type": "text",
                "text": "Diff command timed out"
            }],
            "is_error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": f"Error getting diff: {e}"
            }],
            "is_error": True
        }


@tool("request_clarification", "Request clarification from the user when facing ambiguous situations", {
    "question": str,
    "options": list,
    "context": str,
    "step_id": str,
    "pipeline_id": str
})
async def request_clarification(args: dict[str, Any]) -> dict[str, Any]:
    """
    Request clarification from the user when facing an ambiguous situation.

    This tool suspends the current step and prompts the user to choose from
    multiple options. The pipeline will resume once the user responds.

    Args:
        question: The question to ask the user (clear and specific)
        options: List of 2-4 possible answers (strings)
        context: Explanation of why you're asking and what impact the answer will have
        step_id: The current step ID (from context)
        pipeline_id: The current pipeline ID (from context)

    Returns:
        A response indicating the clarification request was created.
        The actual answer will be provided when the pipeline resumes.
    """
    import aiohttp

    question = args.get("question", "")
    options = args.get("options", [])
    context = args.get("context", "")
    step_id = args.get("step_id", "")
    pipeline_id = args.get("pipeline_id", "")

    # Validate inputs
    if not question:
        return {
            "content": [{
                "type": "text",
                "text": "Error: question is required"
            }],
            "is_error": True
        }

    if not options or len(options) < 2 or len(options) > 4:
        return {
            "content": [{
                "type": "text",
                "text": "Error: must provide 2-4 options"
            }],
            "is_error": True
        }

    if not step_id or not pipeline_id:
        return {
            "content": [{
                "type": "text",
                "text": "Error: step_id and pipeline_id are required"
            }],
            "is_error": True
        }

    try:
        # Call the clarifications API to create the request
        # Since we're inside the backend, we can import and call directly
        from db import get_db, generate_id
        from websocket.manager import broadcast_message
        from datetime import datetime

        db = await get_db()
        now = datetime.utcnow().isoformat()
        clarification_id = generate_id()

        # Get step info
        step = await db.fetchone(
            "SELECT * FROM pipeline_steps WHERE id = ?",
            (step_id,)
        )
        if not step:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: Step {step_id} not found"
                }],
                "is_error": True
            }

        # Create clarification record
        await db.execute("""
            INSERT INTO clarifications (id, step_id, pipeline_id, question, options, context, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (
            clarification_id,
            step_id,
            pipeline_id,
            question,
            json.dumps(options),
            context,
            now
        ))

        # Update step status to waiting
        await db.execute("""
            UPDATE pipeline_steps
            SET status = 'waiting', waiting_for = 'clarification'
            WHERE id = ?
        """, (step_id,))

        # Update pipeline status
        await db.execute("""
            UPDATE pipelines
            SET status = 'needs_user_input'
            WHERE id = ?
        """, (pipeline_id,))

        await db.commit()

        # Broadcast clarification request
        await broadcast_message({
            "type": "clarification_requested",
            "pipeline_id": pipeline_id,
            "step": step["step_number"],
            "clarification_id": clarification_id,
            "question": question,
            "options": options,
            "context": context
        })

        return {
            "content": [{
                "type": "text",
                "text": f"Clarification requested. The pipeline will wait for user input.\n\nQuestion: {question}\nOptions: {', '.join(options)}\n\nThe user will select an answer and the pipeline will resume with their choice."
            }]
        }

    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": f"Error creating clarification request: {e}"
            }],
            "is_error": True
        }


@tool("push_and_comment", "Push current changes and add a comment to the PR", {
    "pr_number": int,
    "commit_message": str,
    "comment_body": str,
    "repo": str
})
async def push_and_comment(args: dict[str, Any]) -> dict[str, Any]:
    """
    Push current changes and add a comment to the PR summarizing what was changed.

    This is useful after addressing review comments to push the fix and
    notify reviewers about the changes made.

    Args:
        pr_number: The PR number
        commit_message: Message for the commit
        comment_body: Comment to add to the PR explaining the changes
        repo: Repository in owner/repo format
    """
    import subprocess

    pr_number = args.get("pr_number")
    commit_message = args.get("commit_message", "Address review comments")
    comment_body = args.get("comment_body", "")
    repo = args.get("repo", "")

    results = []

    try:
        # Stage all changes
        stage_result = subprocess.run(
            ["git", "add", "-A"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if stage_result.returncode != 0:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Failed to stage changes: {stage_result.stderr}"
                }],
                "is_error": True
            }
        results.append("Changes staged")

        # Commit
        commit_result = subprocess.run(
            ["git", "commit", "-m", commit_message],
            capture_output=True,
            text=True,
            timeout=30
        )
        if commit_result.returncode != 0 and "nothing to commit" not in commit_result.stdout:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Failed to commit: {commit_result.stderr or commit_result.stdout}"
                }],
                "is_error": True
            }
        results.append(f"Committed: {commit_message}")

        # Push
        push_result = subprocess.run(
            ["git", "push"],
            capture_output=True,
            text=True,
            timeout=120
        )
        if push_result.returncode != 0:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Failed to push: {push_result.stderr}"
                }],
                "is_error": True
            }
        results.append("Pushed to remote")

        # Add comment to PR
        if comment_body and pr_number:
            cmd = ["gh", "pr", "comment", str(pr_number), "--body", comment_body]
            if repo:
                cmd.extend(["--repo", repo])

            comment_result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "GH_PROMPT_DISABLED": "1"}
            )
            if comment_result.returncode == 0:
                results.append(f"Added comment to PR #{pr_number}")
            else:
                results.append(f"Warning: Failed to add comment: {comment_result.stderr}")

        return {
            "content": [{
                "type": "text",
                "text": "\n".join(results)
            }]
        }

    except subprocess.TimeoutExpired:
        return {
            "content": [{
                "type": "text",
                "text": "Operation timed out"
            }],
            "is_error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": f"Error: {e}"
            }],
            "is_error": True
        }


# Create the MCP server with all pipeline tools
pipeline_mcp_server = create_sdk_mcp_server(
    name="biagent_pipeline",
    version="1.0.0",
    tools=[
        get_ticket_details,
        update_pipeline_status,
        log_agent_action,
        check_git_status,
        create_sandbox_branch,
        run_tests,
        analyze_code_complexity,
        github_cli,
        reply_to_pr_comment,
        get_file_diff,
        push_and_comment,
        request_clarification,
    ]
)


def get_pipeline_mcp_tools() -> list[str]:
    """Get list of MCP tool names for the pipeline."""
    return [
        "mcp__biagent_pipeline__get_ticket_details",
        "mcp__biagent_pipeline__update_pipeline_status",
        "mcp__biagent_pipeline__log_agent_action",
        "mcp__biagent_pipeline__check_git_status",
        "mcp__biagent_pipeline__create_sandbox_branch",
        "mcp__biagent_pipeline__run_tests",
        "mcp__biagent_pipeline__analyze_code_complexity",
        "mcp__biagent_pipeline__github_cli",
        "mcp__biagent_pipeline__reply_to_pr_comment",
        "mcp__biagent_pipeline__get_file_diff",
        "mcp__biagent_pipeline__push_and_comment",
        "mcp__biagent_pipeline__request_clarification",
    ]


def get_review_tools() -> list[str]:
    """Get list of tools useful for code review operations."""
    return [
        "mcp__biagent_pipeline__github_cli",
        "mcp__biagent_pipeline__reply_to_pr_comment",
        "mcp__biagent_pipeline__get_file_diff",
        "mcp__biagent_pipeline__push_and_comment",
        "mcp__biagent_pipeline__check_git_status",
    ]


def get_clarification_tool() -> str:
    """Get the clarification tool name for agents that need user input capability."""
    return "mcp__biagent_pipeline__request_clarification"


def get_agent_tools(agent_type: str) -> list[str]:
    """
    Get the appropriate tools for a specific agent type.

    All agents get basic tools + clarification capability.
    Some agents get specialized tools based on their role.
    """
    # Base tools all agents can use
    base_tools = [
        "mcp__biagent_pipeline__get_ticket_details",
        "mcp__biagent_pipeline__log_agent_action",
        "mcp__biagent_pipeline__request_clarification",
    ]

    # Specialized tools by agent type
    specialized = {
        "context": [],
        "risk": [],
        "planning": [],
        "coding": [
            "mcp__biagent_pipeline__check_git_status",
            "mcp__biagent_pipeline__create_sandbox_branch",
            "mcp__biagent_pipeline__analyze_code_complexity",
        ],
        "testing": [
            "mcp__biagent_pipeline__run_tests",
            "mcp__biagent_pipeline__check_git_status",
        ],
        "docs": [],
        "pr": [
            "mcp__biagent_pipeline__github_cli",
            "mcp__biagent_pipeline__check_git_status",
        ],
        "review": [
            "mcp__biagent_pipeline__github_cli",
            "mcp__biagent_pipeline__reply_to_pr_comment",
            "mcp__biagent_pipeline__get_file_diff",
            "mcp__biagent_pipeline__push_and_comment",
            "mcp__biagent_pipeline__check_git_status",
        ],
    }

    return base_tools + specialized.get(agent_type, [])
