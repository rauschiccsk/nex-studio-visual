"""GitHub repository service — validate existence and create new repos.

The new-project flow uses both surfaces:

* :func:`validate_github_repo` — HEAD-style existence check for the
  legacy path where the user types a pre-existing repo URL.
* :func:`create_github_repo` — provision a new repository under the
  configured organisation when ``POST /api/v1/projects`` ships a
  ``repo_url`` that does not exist yet. The ``projects`` router calls
  this before the DB insert so a failure stops the whole creation
  transactionally.
"""

from __future__ import annotations

import logging

import httpx

from backend.config.settings import settings

logger = logging.getLogger(__name__)

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


def create_github_repo(
    repo: str,
    *,
    description: str = "",
    private: bool = True,
) -> bool:
    """Create a new GitHub repository under the given owner/org.

    Parameters
    ----------
    repo:
        Repository to create in ``owner/repo`` format (e.g.
        ``"rauschiccsk/nex-test"``). The ``owner`` segment is treated
        as an **organisation** — ``POST /orgs/{org}/repos``. The ICC
        GitHub account ``rauschiccsk`` is an organisation, so this is
        the right endpoint for every project we create.
    description:
        Free-text repo description stored on GitHub. Defaults to empty.
    private:
        Whether the repo is private. Defaults to ``True`` — ICC
        projects are private by default.

    Returns
    -------
    bool
        ``True`` when the repo was freshly created (HTTP 201).
        ``False`` when the repo already existed (HTTP 422 with
        GitHub's ``name already exists`` error) — this is treated
        as success on purpose: re-running a POST that previously
        persisted the DB row but failed before commit, or a user
        deliberately reusing an existing repo, should not error.

    Raises
    ------
    ValueError
        If ``repo`` is not in ``owner/repo`` format, or if the owner
        organisation does not exist (HTTP 404 on the create call).
    RuntimeError
        If no ``github_token`` is configured, if the token lacks
        sufficient scope (HTTP 401/403), or if the API returns an
        unexpected status code.
    """
    if not repo or repo.count("/") != 1:
        raise ValueError(f"Invalid repository format '{repo}'. Expected 'owner/repo'.")

    owner, name = repo.split("/")
    if not owner or not name:
        raise ValueError(f"Invalid repository format '{repo}'. Owner and repo name must not be empty.")

    if not settings.github_token:
        raise RuntimeError(
            "Cannot create GitHub repository: no github_token configured. "
            "Set GITHUB_TOKEN in the backend environment."
        )

    url = f"{GITHUB_API_BASE}/orgs/{owner}/repos"
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {settings.github_token}",
    }
    body = {
        "name": name,
        "description": description,
        "private": private,
        "auto_init": False,
    }

    response = httpx.post(
        url, headers=headers, json=body, timeout=settings.github_api_timeout
    )

    if response.status_code == 201:
        logger.info("Created GitHub repository %s", repo)
        return True

    # GitHub returns 422 with an ``errors[].message`` of "name already exists"
    # when a repo with this name is already present under the org. We treat
    # that as non-fatal — the caller wanted a repo, there is one.
    if response.status_code == 422:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        errors = payload.get("errors") or []
        already_exists = any(
            isinstance(e, dict)
            and "already exists" in str(e.get("message", "")).lower()
            for e in errors
        )
        if already_exists:
            logger.info("GitHub repository %s already exists — reusing", repo)
            return False
        raise RuntimeError(
            f"GitHub API rejected repository creation for '{repo}' (422): {response.text}"
        )

    if response.status_code in (401, 403):
        raise RuntimeError(
            f"GitHub API refused repository creation for '{repo}' ({response.status_code}): "
            "token missing or insufficient scope (needs 'repo' / 'admin:org')."
        )

    if response.status_code == 404:
        raise ValueError(
            f"GitHub organisation '{owner}' not found. Check the github_org setting."
        )

    raise RuntimeError(
        f"GitHub API returned unexpected status {response.status_code} "
        f"for repository creation '{repo}': {response.text}"
    )
