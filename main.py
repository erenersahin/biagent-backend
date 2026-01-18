"""
BiAgent Backend - Main FastAPI Application

AI-powered JIRA ticket resolution system with 8 specialized agents.
"""

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from config import settings
from db import get_db, close_db
from api import tickets, pipelines, webhooks, session, worktrees, waitlist, repos
from websocket import manager as ws_manager
from services.jira_sync import start_sync_scheduler, stop_sync_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    print(f"Starting {settings.app_name}...")

    # Initialize database
    await get_db()
    print("Database initialized")

    # Start JIRA sync scheduler
    if settings.jira_base_url:
        await start_sync_scheduler()
        print("JIRA sync scheduler started")

    yield

    # Shutdown
    print(f"Shutting down {settings.app_name}...")
    await stop_sync_scheduler()
    await close_db()


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    description="AI-powered JIRA ticket resolution system with 8 specialized agents",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware - allow all origins in development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # Must be False when using "*"
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(tickets.router, prefix="/api/tickets", tags=["tickets"])
app.include_router(pipelines.router, prefix="/api/pipelines", tags=["pipelines"])
app.include_router(webhooks.router, prefix="/api/webhooks", tags=["webhooks"])
app.include_router(session.router, prefix="/api/session", tags=["session"])
app.include_router(worktrees.router, prefix="/api/worktrees", tags=["worktrees"])
app.include_router(waitlist.router, prefix="/api/waitlist", tags=["waitlist"])
app.include_router(repos.router, prefix="/api/repos", tags=["repos"])

# WebSocket endpoint
app.include_router(ws_manager.router, tags=["websocket"])


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "name": settings.app_name,
        "status": "healthy",
        "version": "0.1.0",
    }


@app.get("/api/health")
async def health():
    """Detailed health check."""
    db = await get_db()
    return {
        "status": "healthy",
        "database": "connected",
        "jira_configured": settings.jira_base_url is not None,
        "github_configured": settings.github_token is not None,
        "anthropic_configured": settings.anthropic_api_key is not None,
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
