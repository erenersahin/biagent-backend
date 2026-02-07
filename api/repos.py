"""
Repos API Router

Endpoints for managing GitHub repositories and repo selection.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import json
from pathlib import Path

from db import get_db, generate_id
from services.github_service import get_github_service, GitHubRepo
from services.worktree_manager import WorktreeManager


router = APIRouter()


class RepoResponse(BaseModel):
    """Response model for a single repository."""
    id: int
    name: str
    full_name: str
    description: Optional[str] = None
    default_branch: str
    clone_url: str
    ssh_url: str
    html_url: str
    private: bool
    language: Optional[str] = None
    updated_at: str


class RepoListResponse(BaseModel):
    """Response model for list of repositories."""
    repos: List[RepoResponse]
    total: int
    source: str  # 'github' or 'local'


class TokenVerifyResponse(BaseModel):
    """Response model for token verification."""
    valid: bool
    user: Optional[str] = None
    name: Optional[str] = None
    scopes: Optional[List[str]] = None
    error: Optional[str] = None


class LocalRepoResponse(BaseModel):
    """Response model for local repository."""
    name: str
    path: str


class LocalRepoListResponse(BaseModel):
    """Response model for local repositories."""
    repos: List[LocalRepoResponse]
    base_path: str


class CombinedReposResponse(BaseModel):
    """Response combining GitHub and local repos."""
    github_repos: List[RepoResponse]
    local_repos: List[LocalRepoResponse]
    github_token_valid: bool


class RegisteredRepoResponse(BaseModel):
    """Response model for a registered repository."""
    id: str
    name: str
    full_name: str
    local_path: str
    clone_url: Optional[str] = None
    ssh_url: Optional[str] = None
    html_url: Optional[str] = None
    default_branch: str = "main"
    language: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True
    github_id: Optional[int] = None
    setup_commands: Optional[List[str]] = None
    created_at: str
    updated_at: str


class RegisterRepoRequest(BaseModel):
    """Request to register a new repository."""
    name: str
    full_name: str
    local_path: str
    clone_url: Optional[str] = None
    ssh_url: Optional[str] = None
    html_url: Optional[str] = None
    default_branch: str = "main"
    language: Optional[str] = None
    description: Optional[str] = None
    github_id: Optional[int] = None
    setup_commands: Optional[List[str]] = None


class UpdateRepoRequest(BaseModel):
    """Request to update a repository."""
    local_path: Optional[str] = None
    default_branch: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    setup_commands: Optional[List[str]] = None


@router.get("/github", response_model=RepoListResponse)
async def list_github_repos(
    include_private: bool = True,
    sort: str = Query("updated", enum=["created", "updated", "pushed", "full_name"]),
):
    """
    List all repositories accessible via the GitHub token.

    This fetches repos directly from GitHub API using the configured token.
    """
    service = get_github_service()

    try:
        repos = await service.list_all_repos(
            include_private=include_private,
            sort=sort,
        )

        return RepoListResponse(
            repos=[
                RepoResponse(
                    id=r.id,
                    name=r.name,
                    full_name=r.full_name,
                    description=r.description,
                    default_branch=r.default_branch,
                    clone_url=r.clone_url,
                    ssh_url=r.ssh_url,
                    html_url=r.html_url,
                    private=r.private,
                    language=r.language,
                    updated_at=r.updated_at,
                )
                for r in repos
            ],
            total=len(repos),
            source="github",
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch repos: {e}")


@router.get("/github/org/{org}", response_model=RepoListResponse)
async def list_org_repos(
    org: str,
    include_private: bool = True,
    sort: str = Query("updated", enum=["created", "updated", "pushed", "full_name"]),
):
    """
    List repositories for a specific GitHub organization.
    """
    service = get_github_service()

    try:
        repos = await service.list_org_repos(
            org=org,
            include_private=include_private,
            sort=sort,
        )

        return RepoListResponse(
            repos=[
                RepoResponse(
                    id=r.id,
                    name=r.name,
                    full_name=r.full_name,
                    description=r.description,
                    default_branch=r.default_branch,
                    clone_url=r.clone_url,
                    ssh_url=r.ssh_url,
                    html_url=r.html_url,
                    private=r.private,
                    language=r.language,
                    updated_at=r.updated_at,
                )
                for r in repos
            ],
            total=len(repos),
            source="github",
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch repos: {e}")


@router.get("/github/{owner}/{repo}", response_model=RepoResponse)
async def get_github_repo(owner: str, repo: str):
    """
    Get details for a specific GitHub repository.
    """
    service = get_github_service()

    try:
        result = await service.get_repo(owner, repo)
        if not result:
            raise HTTPException(status_code=404, detail="Repository not found")

        return RepoResponse(
            id=result.id,
            name=result.name,
            full_name=result.full_name,
            description=result.description,
            default_branch=result.default_branch,
            clone_url=result.clone_url,
            ssh_url=result.ssh_url,
            html_url=result.html_url,
            private=result.private,
            language=result.language,
            updated_at=result.updated_at,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch repo: {e}")


@router.get("/github/verify", response_model=TokenVerifyResponse)
async def verify_github_token():
    """
    Verify the GitHub token is valid and return user/scope info.
    """
    service = get_github_service()
    result = await service.verify_token()

    return TokenVerifyResponse(
        valid=result.get("valid", False),
        user=result.get("user"),
        name=result.get("name"),
        scopes=result.get("scopes"),
        error=result.get("error"),
    )


@router.get("/local", response_model=LocalRepoListResponse)
async def list_local_repos():
    """
    List local git repositories detected in the worktree base path.
    """
    manager = WorktreeManager()
    repos = await manager.detect_repos()

    return LocalRepoListResponse(
        repos=[
            LocalRepoResponse(
                name=name,
                path=str(manager.base_path / name),
            )
            for name in repos
        ],
        base_path=str(manager.base_path),
    )


@router.get("/combined", response_model=CombinedReposResponse)
async def list_combined_repos(
    include_private: bool = True,
    sort: str = Query("updated", enum=["created", "updated", "pushed", "full_name"]),
):
    """
    List both GitHub and local repositories.

    Use this endpoint to get a complete view of available repos
    for selection when creating a pipeline.
    """
    service = get_github_service()
    manager = WorktreeManager()

    # Verify token
    token_result = await service.verify_token()
    token_valid = token_result.get("valid", False)

    # Get GitHub repos
    github_repos = []
    if token_valid:
        try:
            repos = await service.list_all_repos(
                include_private=include_private,
                sort=sort,
            )
            github_repos = [
                RepoResponse(
                    id=r.id,
                    name=r.name,
                    full_name=r.full_name,
                    description=r.description,
                    default_branch=r.default_branch,
                    clone_url=r.clone_url,
                    ssh_url=r.ssh_url,
                    html_url=r.html_url,
                    private=r.private,
                    language=r.language,
                    updated_at=r.updated_at,
                )
                for r in repos
            ]
        except Exception:
            pass

    # Get local repos
    local_repo_names = await manager.detect_repos()
    local_repos = [
        LocalRepoResponse(
            name=name,
            path=str(manager.base_path / name),
        )
        for name in local_repo_names
    ]

    return CombinedReposResponse(
        github_repos=github_repos,
        local_repos=local_repos,
        github_token_valid=token_valid,
    )


# ============================================================
# REGISTERED REPOSITORIES CRUD
# ============================================================

@router.get("/registered", response_model=List[RegisteredRepoResponse])
async def list_registered_repos(
    active_only: bool = Query(True, description="Only return active repositories"),
):
    """
    List all registered repositories.

    Registered repositories are those that have been explicitly added
    for use with BiAgent pipelines.
    """
    db = await get_db()

    if active_only:
        repos = await db.fetchall("""
            SELECT * FROM repositories WHERE is_active = TRUE ORDER BY name
        """)
    else:
        repos = await db.fetchall("SELECT * FROM repositories ORDER BY name")

    return [
        RegisteredRepoResponse(
            id=r["id"],
            name=r["name"],
            full_name=r["full_name"],
            local_path=r["local_path"],
            clone_url=r["clone_url"],
            ssh_url=r["ssh_url"],
            html_url=r["html_url"],
            default_branch=r["default_branch"] or "main",
            language=r["language"],
            description=r["description"],
            is_active=bool(r["is_active"]),
            github_id=r["github_id"],
            setup_commands=json.loads(r["setup_commands"]) if r["setup_commands"] else None,
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in repos
    ]


@router.get("/registered/{repo_id}", response_model=RegisteredRepoResponse)
async def get_registered_repo(repo_id: str):
    """Get a registered repository by ID."""
    db = await get_db()

    repo = await db.fetchone(
        "SELECT * FROM repositories WHERE id = ?",
        (repo_id,)
    )

    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    return RegisteredRepoResponse(
        id=repo["id"],
        name=repo["name"],
        full_name=repo["full_name"],
        local_path=repo["local_path"],
        clone_url=repo["clone_url"],
        ssh_url=repo["ssh_url"],
        html_url=repo["html_url"],
        default_branch=repo["default_branch"] or "main",
        language=repo["language"],
        description=repo["description"],
        is_active=bool(repo["is_active"]),
        github_id=repo["github_id"],
        setup_commands=json.loads(repo["setup_commands"]) if repo["setup_commands"] else None,
        created_at=repo["created_at"],
        updated_at=repo["updated_at"],
    )


@router.post("/repositories", response_model=RegisteredRepoResponse)
async def register_repository(request: RegisterRepoRequest):
    """
    Register a new repository for use with BiAgent.

    This creates a record in the database that associates a local path
    with repository metadata. The local path must exist and be a git repository.
    """
    db = await get_db()

    # Validate local path exists and is a git repo
    path = Path(request.local_path)
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {request.local_path}")

    git_dir = path / ".git"
    if not git_dir.exists():
        raise HTTPException(status_code=400, detail=f"Not a git repository: {request.local_path}")

    # Check if already registered
    existing = await db.fetchone(
        "SELECT id FROM repositories WHERE full_name = ?",
        (request.full_name,)
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Repository {request.full_name} already registered")

    now = datetime.utcnow().isoformat()
    repo_id = generate_id()

    await db.execute("""
        INSERT INTO repositories (
            id, name, full_name, local_path, clone_url, ssh_url, html_url,
            default_branch, language, description, github_id, setup_commands,
            is_active, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?)
    """, (
        repo_id,
        request.name,
        request.full_name,
        request.local_path,
        request.clone_url,
        request.ssh_url,
        request.html_url,
        request.default_branch,
        request.language,
        request.description,
        request.github_id,
        json.dumps(request.setup_commands) if request.setup_commands else None,
        now,
        now,
    ))

    await db.commit()

    return RegisteredRepoResponse(
        id=repo_id,
        name=request.name,
        full_name=request.full_name,
        local_path=request.local_path,
        clone_url=request.clone_url,
        ssh_url=request.ssh_url,
        html_url=request.html_url,
        default_branch=request.default_branch,
        language=request.language,
        description=request.description,
        is_active=True,
        github_id=request.github_id,
        setup_commands=request.setup_commands,
        created_at=now,
        updated_at=now,
    )


@router.post("/repositories/from-github/{owner}/{repo}", response_model=RegisteredRepoResponse)
async def register_from_github(
    owner: str,
    repo: str,
    local_path: str = Query(..., description="Local path where repo is cloned"),
):
    """
    Register a repository by fetching its metadata from GitHub.

    This is a convenience endpoint that fetches repo details from GitHub
    and creates a registration record.
    """
    db = await get_db()
    service = get_github_service()

    # Fetch repo details from GitHub
    github_repo = await service.get_repo(owner, repo)
    if not github_repo:
        raise HTTPException(status_code=404, detail=f"Repository {owner}/{repo} not found on GitHub")

    full_name = github_repo.full_name

    # Check if already registered
    existing = await db.fetchone(
        "SELECT id FROM repositories WHERE full_name = ?",
        (full_name,)
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Repository {full_name} already registered")

    # Validate local path
    path = Path(local_path)
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {local_path}")

    now = datetime.utcnow().isoformat()
    repo_id = generate_id()

    await db.execute("""
        INSERT INTO repositories (
            id, name, full_name, local_path, clone_url, ssh_url, html_url,
            default_branch, language, description, github_id,
            is_active, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?)
    """, (
        repo_id,
        github_repo.name,
        full_name,
        local_path,
        github_repo.clone_url,
        github_repo.ssh_url,
        github_repo.html_url,
        github_repo.default_branch,
        github_repo.language,
        github_repo.description,
        github_repo.id,
        now,
        now,
    ))

    await db.commit()

    return RegisteredRepoResponse(
        id=repo_id,
        name=github_repo.name,
        full_name=full_name,
        local_path=local_path,
        clone_url=github_repo.clone_url,
        ssh_url=github_repo.ssh_url,
        html_url=github_repo.html_url,
        default_branch=github_repo.default_branch,
        language=github_repo.language,
        description=github_repo.description,
        is_active=True,
        github_id=github_repo.id,
        setup_commands=None,
        created_at=now,
        updated_at=now,
    )


@router.patch("/repositories/{repo_id}", response_model=RegisteredRepoResponse)
async def update_repository(repo_id: str, request: UpdateRepoRequest):
    """Update a registered repository."""
    db = await get_db()

    repo = await db.fetchone(
        "SELECT * FROM repositories WHERE id = ?",
        (repo_id,)
    )

    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    updates = []
    params = []

    if request.local_path is not None:
        # Validate new path
        path = Path(request.local_path)
        if not path.exists():
            raise HTTPException(status_code=400, detail=f"Path does not exist: {request.local_path}")
        updates.append("local_path = ?")
        params.append(request.local_path)

    if request.default_branch is not None:
        updates.append("default_branch = ?")
        params.append(request.default_branch)

    if request.description is not None:
        updates.append("description = ?")
        params.append(request.description)

    if request.is_active is not None:
        updates.append("is_active = ?")
        params.append(request.is_active)

    if request.setup_commands is not None:
        updates.append("setup_commands = ?")
        params.append(json.dumps(request.setup_commands))

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    now = datetime.utcnow().isoformat()
    updates.append("updated_at = ?")
    params.append(now)

    params.append(repo_id)

    await db.execute(
        f"UPDATE repositories SET {', '.join(updates)} WHERE id = ?",
        params
    )
    await db.commit()

    # Fetch updated repo
    updated_repo = await db.fetchone(
        "SELECT * FROM repositories WHERE id = ?",
        (repo_id,)
    )

    return RegisteredRepoResponse(
        id=updated_repo["id"],
        name=updated_repo["name"],
        full_name=updated_repo["full_name"],
        local_path=updated_repo["local_path"],
        clone_url=updated_repo["clone_url"],
        ssh_url=updated_repo["ssh_url"],
        html_url=updated_repo["html_url"],
        default_branch=updated_repo["default_branch"] or "main",
        language=updated_repo["language"],
        description=updated_repo["description"],
        is_active=bool(updated_repo["is_active"]),
        github_id=updated_repo["github_id"],
        setup_commands=json.loads(updated_repo["setup_commands"]) if updated_repo["setup_commands"] else None,
        created_at=updated_repo["created_at"],
        updated_at=updated_repo["updated_at"],
    )


@router.delete("/repositories/{repo_id}")
async def delete_repository(
    repo_id: str,
    hard_delete: bool = Query(False, description="Permanently delete instead of soft delete"),
):
    """
    Delete a registered repository.

    By default, this performs a soft delete (sets is_active = FALSE).
    Use hard_delete=true to permanently remove the record.

    Note: This does NOT delete the actual repository from disk.
    """
    db = await get_db()

    repo = await db.fetchone(
        "SELECT * FROM repositories WHERE id = ?",
        (repo_id,)
    )

    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    if hard_delete:
        # Check if repo is in use by any active pipelines
        in_use = await db.fetchone("""
            SELECT COUNT(*) as count FROM worktree_repos wr
            JOIN worktree_sessions ws ON wr.session_id = ws.id
            WHERE wr.repo_name = ? AND ws.status NOT IN ('cleaned', 'failed')
        """, (repo["name"],))

        if in_use and in_use["count"] > 0:
            raise HTTPException(
                status_code=409,
                detail="Repository is in use by active worktree sessions"
            )

        await db.execute("DELETE FROM repositories WHERE id = ?", (repo_id,))
        await db.commit()

        return {"status": "deleted", "id": repo_id, "type": "hard"}
    else:
        await db.execute(
            "UPDATE repositories SET is_active = FALSE, updated_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), repo_id)
        )
        await db.commit()

        return {"status": "deactivated", "id": repo_id, "type": "soft"}
