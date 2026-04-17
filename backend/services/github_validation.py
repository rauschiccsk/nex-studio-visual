"""GitHub Repository Validation Service.

Validates that a GitHub repository exists and is accessible
using the GitHub API with a configured token.
"""

from __future__ import annotations

import httpx

from backend.config.settings import settings

GITHUB_API_BASE = "https://api.github.com"


def validate_github_repo(repo: str) -> bool:
    """Check whether a GitHub repository exists and is accessible.

    Parameters
    ----------
    repo:
        Repository in ``owner/repo`` format (e.g. ``"icc-dev/nex-studio"``).

    Returns
    -------
    bool
        ``True`` if the repository exists (HTTP 200),
        ``False`` if not found (HTTP 404).

    Raises
    ------
    ValueError
        If *repo* format is invalid (must contain exactly one ``/``).
    RuntimeError
        If the GitHub API returns an unexpected status code.
    """
    if not repo or repo.count("/") != 1:
        raise ValueError(f"Invalid repository format '{repo}'. Expected 'owner/repo'.")

    owner, name = repo.split("/")
    if not owner or not name:
        raise ValueError(f"Invalid repository format '{repo}'. Owner and repo name must not be empty.")

    url = f"{GITHUB_API_BASE}/repos/{owner}/{name}"
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    response = httpx.get(url, headers=headers, timeout=settings.github_api_timeout)

    if response.status_code == 200:
        return True
    if response.status_code == 404:
        return False

    raise RuntimeError(
        f"GitHub API returned unexpected status {response.status_code} for repository '{repo}': {response.text}"
    )
