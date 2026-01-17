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
    ]
