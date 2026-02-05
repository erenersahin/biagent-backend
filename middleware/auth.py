"""
Clerk Authentication Middleware

Provides FastAPI dependencies for authentication and authorization.
Supports both consumer (local) and organization (multi-tenant) tiers.
"""

import os
import logging
from typing import Optional, Annotated
from functools import lru_cache

from fastapi import Depends, HTTPException, status, Request, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from schemas.auth import AuthUser, AuthOrganization, TokenPayload

logger = logging.getLogger(__name__)

# Optional Clerk SDK import - gracefully handle if not installed
try:
    from clerk_backend_api import Clerk
    from clerk_backend_api.jwks import AuthenticateRequestOptions
    CLERK_SDK_AVAILABLE = True
except ImportError:
    CLERK_SDK_AVAILABLE = False
    logger.warning("clerk-backend-api not installed. Auth will be disabled.")


# HTTP Bearer token extractor
security = HTTPBearer(auto_error=False)


class ClerkAuth:
    """
    Clerk authentication handler.

    Handles JWT verification and user/org lookup.
    Can be disabled via BIAGENT_AUTH_ENABLED=false for local development.
    """

    def __init__(self):
        self._clerk: Optional["Clerk"] = None
        self._auth_enabled = os.getenv("BIAGENT_AUTH_ENABLED", "false").lower() == "true"
        self._clerk_secret_key = os.getenv("CLERK_SECRET_KEY")
        self._clerk_publishable_key = os.getenv("CLERK_PUBLISHABLE_KEY")

    @property
    def is_enabled(self) -> bool:
        """Check if authentication is enabled."""
        return self._auth_enabled and CLERK_SDK_AVAILABLE and bool(self._clerk_secret_key)

    @property
    def clerk(self) -> Optional["Clerk"]:
        """Get or create Clerk SDK instance."""
        if not self.is_enabled:
            return None

        if self._clerk is None and self._clerk_secret_key:
            self._clerk = Clerk(bearer_auth=self._clerk_secret_key)

        return self._clerk

    async def verify_token(self, token: str) -> Optional[TokenPayload]:
        """
        Verify a JWT token with Clerk.

        Args:
            token: The JWT token from the Authorization header

        Returns:
            TokenPayload if valid, None otherwise
        """
        if not self.is_enabled or not self.clerk:
            return None

        try:
            # Use Clerk SDK to verify the token
            # The SDK handles JWKS fetching and signature verification
            import httpx
            from clerk_backend_api.security import authenticate_request

            # Create a mock request with the token
            mock_request = httpx.Request(
                "GET",
                "https://api.biagent.dev/",
                headers={"Authorization": f"Bearer {token}"}
            )

            request_state = self.clerk.authenticate_request(
                mock_request,
                AuthenticateRequestOptions(
                    authorized_parties=os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",")
                )
            )

            if not request_state.is_signed_in:
                logger.warning(f"Token verification failed: {request_state.reason}")
                return None

            # Extract payload from verified token
            payload = request_state.payload
            if payload:
                return TokenPayload(
                    sub=payload.get("sub", ""),
                    iss=payload.get("iss", ""),
                    aud=payload.get("aud"),
                    exp=payload.get("exp", 0),
                    iat=payload.get("iat", 0),
                    nbf=payload.get("nbf"),
                    azp=payload.get("azp"),
                    sid=payload.get("sid"),
                    org_id=payload.get("org_id"),
                    org_role=payload.get("org_role"),
                    org_slug=payload.get("org_slug"),
                    org_permissions=payload.get("org_permissions"),
                )

            return None

        except Exception as e:
            logger.error(f"Token verification error: {e}")
            return None

    async def get_user_by_clerk_id(self, clerk_user_id: str) -> Optional[dict]:
        """
        Get user details from Clerk API.

        Args:
            clerk_user_id: The Clerk user ID (sub from JWT)

        Returns:
            User details dict or None
        """
        if not self.is_enabled or not self.clerk:
            return None

        try:
            user = self.clerk.users.get(user_id=clerk_user_id)
            if user:
                return {
                    "clerk_user_id": user.id,
                    "email": user.email_addresses[0].email_address if user.email_addresses else None,
                    "name": f"{user.first_name or ''} {user.last_name or ''}".strip() or None,
                }
            return None
        except Exception as e:
            logger.error(f"Failed to get user from Clerk: {e}")
            return None

    async def get_user_organizations(self, clerk_user_id: str) -> list[dict]:
        """
        Get organizations the user belongs to.

        Args:
            clerk_user_id: The Clerk user ID

        Returns:
            List of organization dicts with roles
        """
        if not self.is_enabled or not self.clerk:
            return []

        try:
            memberships = self.clerk.users.get_organization_memberships(user_id=clerk_user_id)
            return [
                {
                    "clerk_org_id": m.organization.id,
                    "name": m.organization.name,
                    "slug": m.organization.slug,
                    "role": m.role,
                }
                for m in (memberships.data if memberships else [])
            ]
        except Exception as e:
            logger.error(f"Failed to get user organizations: {e}")
            return []


# Global auth instance
clerk_auth = ClerkAuth()


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_clerk_org_id: Optional[str] = Header(None, alias="x-clerk-org-id"),
) -> AuthUser:
    """
    FastAPI dependency to get the current authenticated user.

    Raises HTTPException 401 if not authenticated.
    """
    # If auth is disabled, return a default user for local development
    if not clerk_auth.is_enabled:
        return AuthUser(
            id="local-user",
            clerk_user_id="local-user",
            email="local@biagent.dev",
            name="Local Developer",
            organizations=[],
            current_org=None,
        )

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify the token
    token_payload = await clerk_auth.verify_token(credentials.credentials)
    if not token_payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get user details
    user_data = await clerk_auth.get_user_by_clerk_id(token_payload.sub)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Get user organizations
    orgs_data = await clerk_auth.get_user_organizations(token_payload.sub)
    organizations = [
        AuthOrganization(
            id=org.get("clerk_org_id", ""),
            clerk_org_id=org.get("clerk_org_id", ""),
            name=org.get("name", ""),
            slug=org.get("slug", ""),
            role=org.get("role", "member"),
        )
        for org in orgs_data
    ]

    # Determine current org from token or header
    current_org = None
    org_id = x_clerk_org_id or token_payload.org_id
    if org_id:
        current_org = next(
            (org for org in organizations if org.clerk_org_id == org_id),
            None
        )
        # Update role from token if available
        if current_org and token_payload.org_role:
            current_org.role = token_payload.org_role

    # TODO: Look up or create local user record in database
    # For now, use Clerk user ID as the local ID
    return AuthUser(
        id=token_payload.sub,  # Will be replaced with local DB ID
        clerk_user_id=token_payload.sub,
        email=user_data.get("email", ""),
        name=user_data.get("name"),
        organizations=organizations,
        current_org=current_org,
    )


async def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_clerk_org_id: Optional[str] = Header(None, alias="x-clerk-org-id"),
) -> Optional[AuthUser]:
    """
    FastAPI dependency to get the current user if authenticated.

    Returns None if not authenticated (doesn't raise exception).
    """
    if not clerk_auth.is_enabled:
        return AuthUser(
            id="local-user",
            clerk_user_id="local-user",
            email="local@biagent.dev",
            name="Local Developer",
            organizations=[],
            current_org=None,
        )

    if not credentials:
        return None

    try:
        return await get_current_user(request, credentials, x_clerk_org_id)
    except HTTPException:
        return None


async def get_current_org(
    user: AuthUser = Depends(get_current_user),
) -> AuthOrganization:
    """
    FastAPI dependency to get the current organization context.

    Requires the user to have an active organization context.
    """
    if not user.current_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization context required. Set x-clerk-org-id header.",
        )

    return user.current_org


def require_auth(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Dependency that requires authentication."""
    return user


def require_org(org: AuthOrganization = Depends(get_current_org)) -> AuthOrganization:
    """Dependency that requires organization context."""
    return org


def require_role(*allowed_roles: str):
    """
    Factory for creating role-based access dependencies.

    Usage:
        @router.post("/admin-only")
        async def admin_endpoint(org: AuthOrganization = Depends(require_role("owner", "admin"))):
            pass
    """
    async def role_checker(org: AuthOrganization = Depends(get_current_org)) -> AuthOrganization:
        if org.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of these roles: {', '.join(allowed_roles)}",
            )
        return org

    return role_checker


# Type aliases for cleaner dependency injection
CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
CurrentUserOptional = Annotated[Optional[AuthUser], Depends(get_current_user_optional)]
CurrentOrg = Annotated[AuthOrganization, Depends(get_current_org)]
