"""
GitHub Service

Fetches repository list and metadata from GitHub using the configured token.
Provides repo selection for determining which repos are affected by tickets.
"""

import asyncio
import httpx
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from config import settings


@dataclass
class GitHubRepo:
    """A GitHub repository."""
    id: int
    name: str
    full_name: str  # owner/repo format
    description: Optional[str]
    default_branch: str
    clone_url: str
    ssh_url: str
    html_url: str
    private: bool
    language: Optional[str]
    updated_at: str


class GitHubService:
    """Service for interacting with GitHub API."""

    BASE_URL = "https://api.github.com"

    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.github_token
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"

    async def list_repos(
        self,
        include_private: bool = True,
        sort: str = "updated",
        per_page: int = 100,
        page: int = 1,
    ) -> List[GitHubRepo]:
        """
        List repositories accessible to the authenticated user.

        Args:
            include_private: Include private repos (requires appropriate token scope)
            sort: Sort by 'created', 'updated', 'pushed', or 'full_name'
            per_page: Number of results per page (max 100)
            page: Page number for pagination

        Returns:
            List of GitHubRepo objects
        """
        if not self.token:
            return []

        params = {
            "sort": sort,
            "per_page": min(per_page, 100),
            "page": page,
            "visibility": "all" if include_private else "public",
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.BASE_URL}/user/repos",
                    headers=self._headers,
                    params=params,
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                return [
                    GitHubRepo(
                        id=repo["id"],
                        name=repo["name"],
                        full_name=repo["full_name"],
                        description=repo.get("description"),
                        default_branch=repo.get("default_branch", "main"),
                        clone_url=repo["clone_url"],
                        ssh_url=repo["ssh_url"],
                        html_url=repo["html_url"],
                        private=repo["private"],
                        language=repo.get("language"),
                        updated_at=repo["updated_at"],
                    )
                    for repo in data
                ]
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    raise ValueError("Invalid or expired GitHub token")
                raise
            except Exception as e:
                raise RuntimeError(f"Failed to fetch repos from GitHub: {e}")

    async def list_all_repos(
        self,
        include_private: bool = True,
        sort: str = "updated",
    ) -> List[GitHubRepo]:
        """
        List all repositories (handles pagination automatically).

        Args:
            include_private: Include private repos
            sort: Sort order

        Returns:
            Complete list of all accessible repos
        """
        all_repos = []
        page = 1

        while True:
            repos = await self.list_repos(
                include_private=include_private,
                sort=sort,
                per_page=100,
                page=page,
            )
            if not repos:
                break
            all_repos.extend(repos)
            if len(repos) < 100:
                break
            page += 1

        return all_repos

    async def get_repo(self, owner: str, repo: str) -> Optional[GitHubRepo]:
        """
        Get a specific repository.

        Args:
            owner: Repository owner
            repo: Repository name

        Returns:
            GitHubRepo or None if not found
        """
        if not self.token:
            return None

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}",
                    headers=self._headers,
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                return GitHubRepo(
                    id=data["id"],
                    name=data["name"],
                    full_name=data["full_name"],
                    description=data.get("description"),
                    default_branch=data.get("default_branch", "main"),
                    clone_url=data["clone_url"],
                    ssh_url=data["ssh_url"],
                    html_url=data["html_url"],
                    private=data["private"],
                    language=data.get("language"),
                    updated_at=data["updated_at"],
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return None
                raise

    async def list_org_repos(
        self,
        org: str,
        include_private: bool = True,
        sort: str = "updated",
    ) -> List[GitHubRepo]:
        """
        List repositories for a specific organization.

        Args:
            org: Organization name
            include_private: Include private repos
            sort: Sort order

        Returns:
            List of GitHubRepo objects
        """
        if not self.token:
            return []

        all_repos = []
        page = 1

        params = {
            "sort": sort,
            "per_page": 100,
            "type": "all" if include_private else "public",
        }

        async with httpx.AsyncClient() as client:
            while True:
                params["page"] = page
                try:
                    response = await client.get(
                        f"{self.BASE_URL}/orgs/{org}/repos",
                        headers=self._headers,
                        params=params,
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    data = response.json()

                    if not data:
                        break

                    all_repos.extend([
                        GitHubRepo(
                            id=repo["id"],
                            name=repo["name"],
                            full_name=repo["full_name"],
                            description=repo.get("description"),
                            default_branch=repo.get("default_branch", "main"),
                            clone_url=repo["clone_url"],
                            ssh_url=repo["ssh_url"],
                            html_url=repo["html_url"],
                            private=repo["private"],
                            language=repo.get("language"),
                            updated_at=repo["updated_at"],
                        )
                        for repo in data
                    ])

                    if len(data) < 100:
                        break
                    page += 1
                except httpx.HTTPStatusError:
                    break

        return all_repos

    async def verify_token(self) -> Dict[str, Any]:
        """
        Verify the GitHub token is valid and return user/scope info.

        Returns:
            Dict with 'valid', 'user', 'scopes' keys
        """
        if not self.token:
            return {"valid": False, "error": "No token configured"}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.BASE_URL}/user",
                    headers=self._headers,
                    timeout=30.0,
                )
                response.raise_for_status()
                user = response.json()

                # Get scopes from response headers
                scopes = response.headers.get("X-OAuth-Scopes", "").split(", ")

                return {
                    "valid": True,
                    "user": user.get("login"),
                    "name": user.get("name"),
                    "scopes": [s.strip() for s in scopes if s.strip()],
                }
            except httpx.HTTPStatusError as e:
                return {
                    "valid": False,
                    "error": f"HTTP {e.response.status_code}: {e.response.text}",
                }
            except Exception as e:
                return {"valid": False, "error": str(e)}


# Singleton instance
_github_service: Optional[GitHubService] = None


def get_github_service() -> GitHubService:
    """Get or create the GitHub service singleton."""
    global _github_service
    if _github_service is None:
        _github_service = GitHubService()
    return _github_service
