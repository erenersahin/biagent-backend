# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BiAgent is an AI-powered JIRA ticket resolution system with an 8-step pipeline. Developers select JIRA tickets and watch AI agents work through: Context → Risk → Planning → Coding → Testing → Docs → PR → Review.

## Commands

### Backend (from `backend/` directory)
```bash
# Run the server (default port 8000)
python main.py

# Run tests
pytest

# Install CLI tool for development
pip install -e .
biagent-jira list
```

### Frontend (from `frontend/` directory)
```bash
npm run dev      # Dev server on port 3000, proxies /api to localhost:8888
npm run build    # TypeScript compile + Vite build
npm run lint     # ESLint with zero warnings allowed
```

## Architecture

### Backend Stack
- **FastAPI** with async/await throughout
- **SQLite** (aiosqlite) for persistence - database at `data/biagent.db`
- **Anthropic SDK** for Claude agents
- **WebSocket** at `/ws` for real-time token streaming
- **Pydantic Settings** with `BIAGENT_` env prefix

### Frontend Stack
- **React 18** + TypeScript + Vite
- **Zustand** for state management (single store in `lib/store.ts`)
- **Tailwind CSS** with Blacksmith design system (neon yellow `#F0FB29`, dark `#202020`)
- API proxy: frontend dev server proxies `/api` and `/ws` to backend

### Agent Pipeline
Each ticket runs through 8 sequential steps. Configuration in `backend/config.py`:
1. Context Agent - gathers ticket details and codebase context
2. Risk Agent - analyzes blockers and dependencies
3. Planning Agent - creates implementation plan
4. Coding Agent - implements on sandbox branch
5. Testing Agent - writes/runs tests
6. Docs Agent (uses Haiku) - updates documentation
7. PR Agent - creates pull request
8. Review Agent - handles PR feedback

### Key Patterns
- **API Routers**: `api/tickets.py`, `api/pipelines.py`, `api/webhooks.py`, `api/session.py`
- **Agents**: Each in `agents/` extends `BaseAgent` from `agents/base.py`
- **WebSocket Messages**: `pipeline_started`, `step_started`, `token`, `step_completed`, `tool_call_started`
- **State Flow**: Frontend Zustand store ↔ FastAPI ↔ SQLite, with WebSocket for streaming

### Database Tables
- `tickets` - JIRA ticket cache
- `pipelines` - pipeline execution records
- `pipeline_steps` - individual step status and cost tracking
- `step_outputs` - artifacts from completed steps
- `step_events` - chronological events for streaming display

## Configuration

Environment variables use `BIAGENT_` prefix. Key settings:
- `BIAGENT_PORT` (default 8000), `BIAGENT_DEBUG`
- `BIAGENT_JIRA_BASE_URL`, `BIAGENT_JIRA_EMAIL`, `BIAGENT_JIRA_API_TOKEN`
- `BIAGENT_GITHUB_TOKEN`, `BIAGENT_GITHUB_REPO`
- `BIAGENT_ANTHROPIC_API_KEY`
- `BIAGENT_CODEBASE_PATH` - target codebase for agents to modify
