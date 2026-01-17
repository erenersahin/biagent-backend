#!/usr/bin/env python3
"""Print OAuth token from Claude Code credentials."""

import json
from pathlib import Path

def get_oauth_token() -> str:
    """Extract OAuth token from Claude Code credentials file."""
    cred_path = Path.home() / ".claude" / ".credentials.json"

    if not cred_path.exists():
        raise FileNotFoundError(f"Credentials file not found: {cred_path}")

    with open(cred_path, "r") as f:
        data = json.load(f)

    oauth = data.get("claudeAiOauth", data)
    token = oauth.get("accessToken") or oauth.get("access_token")

    if not token:
        raise ValueError("No access token found in credentials")

    return token

if __name__ == "__main__":
    try:
        token = get_oauth_token()
        print(f"oauth token:{token}")
    except Exception as e:
        print(f"Error: {e}")
