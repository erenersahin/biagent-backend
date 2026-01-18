"""
Setup Detector Service

AI-powered detection of repository setup commands using Claude Haiku.
Analyzes README, Dockerfile, package.json, etc. to determine how to set up a repo.
"""

import json
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass
from enum import Enum

import anthropic

from config import settings


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class SetupResult:
    """Result of setup detection."""
    commands: Optional[List[str]]
    confidence: Confidence
    needs_user_input: bool
    files_checked: List[str]
    reasoning: Optional[str] = None


# Files to check for setup instructions
SETUP_FILES = [
    "README.md",
    "CONTRIBUTING.md",
    "DEVELOPMENT.md",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Makefile",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Pipfile.lock",
    ".env.example",
    ".env.sample",
    "scripts/setup.sh",
    "scripts/install.sh",
    "bin/setup",
]


def read_file_if_exists(path: Path, max_chars: int = 3000) -> Optional[str]:
    """Read a file if it exists, truncating to max_chars."""
    if path.exists() and path.is_file():
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            if len(content) > max_chars:
                return content[:max_chars] + "\n... [truncated]"
            return content
        except Exception:
            return None
    return None


class SetupDetector:
    """Detects setup commands for a repository using AI analysis."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    async def detect_setup(self, repo_path: Path) -> SetupResult:
        """
        Analyze a repository and determine setup commands.

        Args:
            repo_path: Path to the repository root

        Returns:
            SetupResult with detected commands and confidence level
        """
        # Collect file contents
        files_found = {}
        files_checked = []

        for filename in SETUP_FILES:
            file_path = repo_path / filename
            content = read_file_if_exists(file_path)
            if content:
                files_found[filename] = content
                files_checked.append(filename)

        if not files_found:
            return SetupResult(
                commands=None,
                confidence=Confidence.LOW,
                needs_user_input=True,
                files_checked=[],
                reasoning="No setup-related files found in repository"
            )

        # Build prompt for Claude Haiku
        prompt = self._build_prompt(files_found)

        try:
            response = self.client.messages.create(
                model="claude-haiku-3-5-20241022",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )

            result = self._parse_response(response.content[0].text, files_checked)
            return result

        except Exception as e:
            return SetupResult(
                commands=None,
                confidence=Confidence.LOW,
                needs_user_input=True,
                files_checked=files_checked,
                reasoning=f"AI analysis failed: {str(e)}"
            )

    def _build_prompt(self, files: dict) -> str:
        """Build the prompt for Claude Haiku."""
        files_content = "\n\n".join([
            f"=== {filename} ===\n{content}"
            for filename, content in files.items()
        ])

        return f"""Analyze this repository and determine the setup commands needed after cloning.

Repository files:
{files_content}

Based on these files, determine:
1. What commands should be run to set up this project after cloning
2. Your confidence level in this detection (high/medium/low)

Consider:
- Package manager detection (npm, yarn, pnpm, pip, pipenv, poetry)
- Environment file setup (copying .env.example to .env)
- Database migrations if mentioned
- Any build or compile steps

Return your response as JSON in this exact format:
{{
    "commands": ["command1", "command2", ...],
    "confidence": "high" | "medium" | "low",
    "reasoning": "Brief explanation of why these commands"
}}

Rules:
- Only include commands that are clearly needed
- If the package.json has a "prepare" or "postinstall" script, npm install will run it
- Don't include commands like "git clone" - assume the repo is already cloned
- Include copying .env.example to .env if .env.example exists
- Set confidence to "low" if you're unsure or the setup is complex/unclear
- Set confidence to "high" only if setup instructions are clear and straightforward"""

    def _parse_response(self, response_text: str, files_checked: List[str]) -> SetupResult:
        """Parse Claude's response into a SetupResult."""
        try:
            # Try to extract JSON from the response
            # Handle case where response might have markdown code blocks
            json_text = response_text
            if "```json" in response_text:
                start = response_text.find("```json") + 7
                end = response_text.find("```", start)
                json_text = response_text[start:end].strip()
            elif "```" in response_text:
                start = response_text.find("```") + 3
                end = response_text.find("```", start)
                json_text = response_text[start:end].strip()

            data = json.loads(json_text)

            confidence = Confidence(data.get("confidence", "low"))
            commands = data.get("commands", [])
            reasoning = data.get("reasoning", "")

            # Filter out empty commands
            commands = [cmd.strip() for cmd in commands if cmd and cmd.strip()]

            return SetupResult(
                commands=commands if commands else None,
                confidence=confidence,
                needs_user_input=confidence == Confidence.LOW,
                files_checked=files_checked,
                reasoning=reasoning
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            return SetupResult(
                commands=None,
                confidence=Confidence.LOW,
                needs_user_input=True,
                files_checked=files_checked,
                reasoning=f"Failed to parse AI response: {str(e)}"
            )

    def detect_package_manager(self, repo_path: Path) -> str:
        """
        Detect which package manager a repository uses.

        Returns: npm, yarn, pnpm, pip, pipenv, poetry, or unknown
        """
        if (repo_path / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (repo_path / "yarn.lock").exists():
            return "yarn"
        if (repo_path / "package-lock.json").exists():
            return "npm"
        if (repo_path / "package.json").exists():
            return "npm"  # Default for Node.js projects
        if (repo_path / "Pipfile").exists():
            return "pipenv"
        if (repo_path / "pyproject.toml").exists():
            # Check if it's poetry
            content = read_file_if_exists(repo_path / "pyproject.toml")
            if content and "[tool.poetry]" in content:
                return "poetry"
            return "pip"
        if (repo_path / "requirements.txt").exists():
            return "pip"
        if (repo_path / "setup.py").exists():
            return "pip"

        return "unknown"

    def get_default_commands(self, repo_path: Path) -> List[str]:
        """
        Get default setup commands based on detected package manager.
        Used as a fallback when AI detection has medium confidence.
        """
        commands = []

        # Check for .env.example
        if (repo_path / ".env.example").exists():
            commands.append("cp .env.example .env")
        elif (repo_path / ".env.sample").exists():
            commands.append("cp .env.sample .env")

        # Detect and add package manager install
        pm = self.detect_package_manager(repo_path)

        if pm == "npm":
            commands.append("npm install")
        elif pm == "yarn":
            commands.append("yarn install")
        elif pm == "pnpm":
            commands.append("pnpm install")
        elif pm == "pip":
            if (repo_path / "requirements.txt").exists():
                commands.append("pip install -r requirements.txt")
            elif (repo_path / "pyproject.toml").exists():
                commands.append("pip install -e .")
        elif pm == "pipenv":
            commands.append("pipenv install")
        elif pm == "poetry":
            commands.append("poetry install")

        return commands
