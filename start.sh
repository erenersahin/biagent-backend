#!/bin/bash
# BiAgent Backend Startup Script
# Extracts OAuth token from Claude Code credentials and starts the server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
ENV_FILE="$SCRIPT_DIR/.env"
CREDENTIALS_FILE="$HOME/.claude/.credentials.json"

echo "🔧 BiAgent Backend Startup"
echo "=========================="

# Check if venv exists
if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ Virtual environment not found at $SCRIPT_DIR/.venv"
    echo "   Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# # Extract OAuth token from Claude Code credentials
# if [ -f "$CREDENTIALS_FILE" ]; then
#     echo "🔑 Extracting OAuth token from Claude Code credentials..."
#     OAUTH_TOKEN=$($VENV_PYTHON -c "
# import json
# with open('$CREDENTIALS_FILE') as f:
#     data = json.load(f)
#     print(data.get('claudeAiOauth', {}).get('accessToken', ''))
# ")

#     if [ -n "$OAUTH_TOKEN" ]; then
#         echo "✓ OAuth token found"

#         # Update or add BIAGENT_ANTHROPIC_AUTH_TOKEN in .env
#         if [ -f "$ENV_FILE" ]; then
#             # Remove existing BIAGENT_ANTHROPIC_AUTH_TOKEN line if present
#             grep -v "^BIAGENT_ANTHROPIC_AUTH_TOKEN=" "$ENV_FILE" > "$ENV_FILE.tmp" || true
#             mv "$ENV_FILE.tmp" "$ENV_FILE"
#         fi

#         # Add the OAuth token
#         echo "BIAGENT_ANTHROPIC_AUTH_TOKEN=$OAUTH_TOKEN" >> "$ENV_FILE"
#         echo "ANTHROPIC_AUTH_TOKEN=$OAUTH_TOKEN" >> "$ENV_FILE"
#         echo "✓ OAuth token added to .env"
#     else
#         echo "⚠ No OAuth token found in credentials file"
#     fi
# else
#     echo "⚠ Claude Code credentials not found at $CREDENTIALS_FILE"
#     echo "  Run 'claude' CLI to authenticate first"
# fi

# Update port to 8888 in .env
if [ -f "$ENV_FILE" ]; then
    # Update BIAGENT_PORT if it exists, otherwise add it
    if grep -q "^BIAGENT_PORT=" "$ENV_FILE"; then
        sed -i 's/^BIAGENT_PORT=.*/BIAGENT_PORT=8888/' "$ENV_FILE"
    else
        echo "BIAGENT_PORT=8888" >> "$ENV_FILE"
    fi

    # Ensure host is 0.0.0.0
    if grep -q "^BIAGENT_HOST=" "$ENV_FILE"; then
        sed -i 's/^BIAGENT_HOST=.*/BIAGENT_HOST=0.0.0.0/' "$ENV_FILE"
    else
        echo "BIAGENT_HOST=0.0.0.0" >> "$ENV_FILE"
    fi
fi

echo ""
echo "🚀 Starting BiAgent Backend on 0.0.0.0:8888..."
echo ""

cd "$SCRIPT_DIR"
exec $VENV_PYTHON main.py
