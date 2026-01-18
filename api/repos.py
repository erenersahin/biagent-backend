"""
Repos API Router

Endpoints for managing GitHub repositories and repo selection.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

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
