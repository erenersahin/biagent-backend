"""
BiAgent Backend - Main FastAPI Application

AI-powered JIRA ticket resolution system with 8 specialized agents.
Supports both consumer (local) and organization (multi-tenant) tiers.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from config import settings
from db import get_db, close_db
from api import tickets, pipelines, webhooks, session, worktrees, waitlist, repos, organizations, usage
from websocket import manager as ws_manager
from services.jira_sync import start_sync_scheduler, stop_sync_scheduler

# Import auth middleware
from middleware.auth import clerk_auth, get_current_user_optional, CurrentUserOptional
from schemas.auth import AuthStatusResponse

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info(f"Starting {settings.app_name} (tier: {settings.tier})...")

    # Initialize legacy SQLite database (for backwards compatibility)
    await get_db()
    logger.info("Database initialized")

    # Initialize SQLAlchemy (for new ORM models)
    try:
        from models import configure_database, create_all_tables
        configure_database(settings.effective_database_url, echo=settings.debug)
        await create_all_tables()
        logger.info("SQLAlchemy models initialized")
    except Exception as e:
        logger.warning(f"SQLAlchemy initialization skipped: {e}")

    # Log auth status
    if clerk_auth.is_enabled:
        logger.info("Clerk authentication enabled")
    else:
        logger.info("Authentication disabled (local development mode)")

    # Start JIRA sync scheduler
    if settings.jira_base_url:
        await start_sync_scheduler()
        logger.info("JIRA sync scheduler started")

    yield

    # Shutdown
    logger.info(f"Shutting down {settings.app_name}...")
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
# app.include_router(tickets.router, prefix="/api/tickets", tags=["tickets"])
# app.include_router(pipelines.router, prefix="/api/pipelines", tags=["pipelines"])
# app.include_router(webhooks.router, prefix="/api/webhooks", tags=["webhooks"])
# app.include_router(session.router, prefix="/api/session", tags=["session"])
# app.include_router(worktrees.router, prefix="/api/worktrees", tags=["worktrees"])
app.include_router(waitlist.router, prefix="/api/waitlist", tags=["waitlist"])
# app.include_router(repos.router, prefix="/api/repos", tags=["repos"])
# app.include_router(organizations.router, prefix="/api/organizations", tags=["organizations"])
app.include_router(usage.router, prefix="/api/usage", tags=["usage"])

# WebSocket endpoint
app.include_router(ws_manager.router, tags=["websocket"])


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "name": settings.app_name,
        "status": "healthy",
        "version": "0.1.0",
        "tier": settings.tier,
    }


@app.get("/api/health")
async def health():
    """Detailed health check."""
    db = await get_db()
    return {
        "status": "healthy",
        "database": "connected",
        "tier": settings.tier,
        "auth_enabled": clerk_auth.is_enabled,
        "jira_configured": settings.jira_base_url is not None,
        "github_configured": settings.github_token is not None,
        "anthropic_configured": settings.anthropic_api_key is not None,
    }


@app.get("/api/auth/status", response_model=AuthStatusResponse)
async def auth_status(user: CurrentUserOptional = None):
    """Get current authentication status."""
    from schemas.auth import AuthUser, SessionInfo

    if user:
        return AuthStatusResponse(
            authenticated=True,
            user=user,
            auth_enabled=clerk_auth.is_enabled,
            tier=settings.tier,
        )

    return AuthStatusResponse(
        authenticated=False,
        auth_enabled=clerk_auth.is_enabled,
        tier=settings.tier,
    )


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
