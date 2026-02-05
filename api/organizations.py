"""
Organizations API

Multi-tenant organization management endpoints.
Only active when auth is enabled (organization tier).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from datetime import datetime
import logging

from middleware.auth import (
    get_current_user,
    get_current_org,
    require_role,
    CurrentUser,
    CurrentOrg,
    clerk_auth,
)
from schemas.organization import (
    OrganizationResponse,
    OrgMemberResponse,
    OrgCredentialCreate,
    OrgCredentialResponse,
    UsageAggregateResponse,
)
from db import get_db, generate_id
from services.vault_client import encrypt_org_credential, decrypt_org_credential

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=List[OrganizationResponse])
async def list_organizations(user: CurrentUser):
    """
    List organizations the current user belongs to.
    """
    if not clerk_auth.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization features require auth to be enabled"
        )

    # Return organizations from the user's auth context
    orgs = []
    for org in user.organizations:
        orgs.append(OrganizationResponse(
            id=org.id,
            clerk_org_id=org.clerk_org_id,
            name=org.name,
            slug=org.slug,
            plan="trial",  # TODO: Look up from database
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))

    return orgs


@router.get("/current", response_model=OrganizationResponse)
async def get_current_organization(org: CurrentOrg):
    """
    Get the current organization context.
    """
    return OrganizationResponse(
        id=org.id,
        clerk_org_id=org.clerk_org_id,
        name=org.name,
        slug=org.slug,
        plan="trial",  # TODO: Look up from database
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


@router.get("/current/members", response_model=List[OrgMemberResponse])
async def list_organization_members(
    org: CurrentOrg,
    user: CurrentUser,
):
    """
    List members of the current organization.
    Requires admin or owner role.
    """
    if org.role not in ["owner", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires admin or owner role"
        )

    # TODO: Look up members from database
    # For now, return empty list
    return []


@router.get("/current/credentials", response_model=List[OrgCredentialResponse])
async def list_organization_credentials(
    org: CurrentOrg,
    user: CurrentUser,
):
    """
    List configured credentials for the current organization.
    Does NOT return the actual credential values.
    """
    if org.role not in ["owner", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires admin or owner role"
        )

    db = await get_db()

    # Ensure table exists
    await db.execute("""
        CREATE TABLE IF NOT EXISTS org_credentials (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            credential_type TEXT NOT NULL,
            encrypted_data BLOB NOT NULL,
            key_id TEXT NOT NULL,
            algorithm TEXT DEFAULT 'fernet',
            created_by TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(org_id, credential_type)
        )
    """)
    await db.commit()

    cursor = await db.execute(
        "SELECT id, org_id, credential_type, created_at FROM org_credentials WHERE org_id = ?",
        (org.id,)
    )
    rows = await cursor.fetchall()

    return [
        OrgCredentialResponse(
            id=row[0],
            org_id=row[1],
            credential_type=row[2],
            created_at=datetime.fromisoformat(row[3]) if row[3] else datetime.utcnow(),
            is_configured=True,
        )
        for row in rows
    ]


@router.post("/current/credentials", response_model=OrgCredentialResponse)
async def create_organization_credential(
    credential: OrgCredentialCreate,
    org: CurrentOrg,
    user: CurrentUser,
):
    """
    Store a credential for the organization.
    Credentials are encrypted before storage.
    """
    if org.role not in ["owner", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires admin or owner role"
        )

    db = await get_db()

    # Ensure table exists
    await db.execute("""
        CREATE TABLE IF NOT EXISTS org_credentials (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            credential_type TEXT NOT NULL,
            encrypted_data BLOB NOT NULL,
            key_id TEXT NOT NULL,
            algorithm TEXT DEFAULT 'fernet',
            created_by TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(org_id, credential_type)
        )
    """)
    await db.commit()

    # Encrypt the credential data
    encrypted = await encrypt_org_credential(
        org_id=org.id,
        credential_type=credential.credential_type,
        data=credential.data,
    )

    credential_id = generate_id()
    now = datetime.utcnow().isoformat()

    # Upsert the credential
    await db.execute("""
        INSERT INTO org_credentials (id, org_id, credential_type, encrypted_data, key_id, algorithm, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(org_id, credential_type) DO UPDATE SET
            encrypted_data = excluded.encrypted_data,
            key_id = excluded.key_id,
            algorithm = excluded.algorithm,
            updated_at = excluded.updated_at
    """, (credential_id, org.id, credential.credential_type, encrypted.encrypted_data,
          encrypted.key_id, encrypted.algorithm, user.id, now, now))
    await db.commit()

    logger.info(f"Stored {credential.credential_type} credential for org {org.id}")

    return OrgCredentialResponse(
        id=credential_id,
        org_id=org.id,
        credential_type=credential.credential_type,
        created_at=datetime.utcnow(),
        is_configured=True,
    )


@router.delete("/current/credentials/{credential_type}")
async def delete_organization_credential(
    credential_type: str,
    org: CurrentOrg,
    user: CurrentUser,
):
    """
    Delete a credential for the organization.
    """
    if org.role not in ["owner", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires admin or owner role"
        )

    db = await get_db()

    # Delete the credential
    await db.execute(
        "DELETE FROM org_credentials WHERE org_id = ? AND credential_type = ?",
        (org.id, credential_type)
    )
    await db.commit()

    logger.info(f"Deleted {credential_type} credential for org {org.id}")

    return {"status": "deleted", "credential_type": credential_type}


@router.get("/current/usage", response_model=List[UsageAggregateResponse])
async def get_organization_usage(
    org: CurrentOrg,
    months: int = 6,
):
    """
    Get usage statistics for the current organization.
    """
    from api.usage import get_monthly_usage
    return await get_monthly_usage(org_id=org.id, months=months)


@router.get("/current/usage/current-month")
async def get_current_month_usage(org: CurrentOrg):
    """
    Get usage for the current billing period.
    """
    from api.usage import get_current_month_stats
    return await get_current_month_stats(org_id=org.id)


# === Internal Functions for Pipeline Engine ===


async def get_org_credential(org_id: str, credential_type: str) -> Optional[dict]:
    """
    Get decrypted credential for an organization.

    Used internally by the pipeline engine to get JIRA/GitHub/Anthropic credentials.

    Args:
        org_id: Organization ID
        credential_type: Type of credential (jira, github, anthropic)

    Returns:
        Decrypted credential data or None if not found
    """
    db = await get_db()

    cursor = await db.execute(
        "SELECT encrypted_data, key_id, algorithm FROM org_credentials WHERE org_id = ? AND credential_type = ?",
        (org_id, credential_type)
    )
    row = await cursor.fetchone()

    if not row:
        return None

    try:
        return await decrypt_org_credential(
            org_id=org_id,
            encrypted_data=row[0],
            key_id=row[1],
            algorithm=row[2] or "fernet",
        )
    except Exception as e:
        logger.error(f"Failed to decrypt {credential_type} credential for org {org_id}: {e}")
        return None


async def get_org_jira_config(org_id: str) -> Optional[dict]:
    """Get JIRA configuration for an organization."""
    cred = await get_org_credential(org_id, "jira")
    if cred:
        return {
            "base_url": cred.get("base_url"),
            "email": cred.get("email"),
            "api_token": cred.get("api_token"),
            "project_key": cred.get("project_key"),
        }
    return None


async def get_org_github_token(org_id: str) -> Optional[str]:
    """Get GitHub token for an organization."""
    cred = await get_org_credential(org_id, "github")
    if cred:
        return cred.get("token")
    return None


async def get_org_anthropic_key(org_id: str) -> Optional[str]:
    """Get Anthropic API key for an organization."""
    cred = await get_org_credential(org_id, "anthropic")
    if cred:
        return cred.get("api_key")
    return None
