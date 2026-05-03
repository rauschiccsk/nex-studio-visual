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

# Safety fallback used when a caller does not resolve the timeout from
# :mod:`backend.services.system_setting` (key
# ``github_api_timeout_seconds``). All routes that invoke these
# functions pass an explicit ``timeout`` sourced from the DB.
_DEFAULT_GITHUB_API_TIMEOUT = 10.0


def validate_github_repo(repo: str, *, timeout: float = _DEFAULT_GITHUB_API_TIMEOUT) -> bool:
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

    response = httpx.get(url, headers=headers, timeout=timeout)

    if response.status_code == 200:
        return True
    if response.status_code == 404:
        return False

    raise RuntimeError(
        f"GitHub API returned unexpected status {response.status_code} for repository '{repo}': {response.text}"
    )


def _github_headers() -> dict[str, str]:
    """Standard GitHub API headers including the bearer token."""
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {settings.github_token}",
    }


def _resolve_repo_endpoint(owner: str, timeout: float) -> str:
    """Return the correct ``POST .../repos`` URL for the owner.

    GitHub splits repo creation across two endpoints — ``/orgs/{org}/
    repos`` for organisations and ``/user/repos`` for the token-owning
    user account. We probe ``GET /users/{owner}`` to read the
    ``type`` field and choose the right one; for a User owner we
    additionally verify the token's own ``login`` matches, since
    ``/user/repos`` will otherwise silently create the repo under the
    token owner instead of the requested user.

    Raises
    ------
    ValueError
        If the account does not exist, or if the owner is a User but
        does not match the token owner.
    RuntimeError
        On unexpected GitHub API response codes.
    """
    probe_url = f"{GITHUB_API_BASE}/users/{owner}"
    probe = httpx.get(probe_url, headers=_github_headers(), timeout=timeout)
    if probe.status_code == 404:
        raise ValueError(f"GitHub account '{owner}' not found. Check the github_org setting.")
    if probe.status_code != 200:
        raise RuntimeError(
            f"GitHub API returned unexpected status {probe.status_code} while probing account '{owner}': {probe.text}"
        )
    account_type = probe.json().get("type")

    if account_type == "Organization":
        return f"{GITHUB_API_BASE}/orgs/{owner}/repos"

    if account_type == "User":
        # /user/repos implicitly uses the token owner; refuse to run
        # when the requested user does not match, rather than silently
        # creating the repo under the wrong account.
        who_resp = httpx.get(
            f"{GITHUB_API_BASE}/user",
            headers=_github_headers(),
            timeout=timeout,
        )
        if who_resp.status_code != 200:
            raise RuntimeError(
                f"GitHub API returned {who_resp.status_code} while identifying the token owner: {who_resp.text}"
            )
        token_login = who_resp.json().get("login")
        if token_login != owner:
            raise ValueError(
                f"Cannot create repository under user '{owner}' — the configured "
                f"github_token belongs to '{token_login}'. Use a token owned by "
                f"'{owner}' or host the repo under an organisation."
            )
        return f"{GITHUB_API_BASE}/user/repos"

    raise RuntimeError(f"GitHub account '{owner}' has unexpected type {account_type!r}; cannot decide create endpoint.")


def create_github_repo(
    repo: str,
    *,
    description: str = "",
    private: bool = True,
    timeout: float = _DEFAULT_GITHUB_API_TIMEOUT,
) -> bool:
    """Create a new GitHub repository under the given owner.

    The owner may be either a **GitHub organisation** or the **token-
    owning user account**. The function probes ``GET /users/{owner}``
    to tell them apart, then picks ``POST /orgs/{owner}/repos`` or
    ``POST /user/repos`` accordingly.

    Parameters
    ----------
    repo:
        Repository to create in ``owner/repo`` format, e.g.
        ``"rauschiccsk/nex-test"``.
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
        GitHub's ``name already exists`` error) — re-running a POST
        that previously persisted the DB row but failed before
        commit, or a user deliberately reusing an existing repo,
        should not error.

    Raises
    ------
    ValueError
        If ``repo`` is not in ``owner/repo`` format, if the owner
        account does not exist, or if the owner is a User account
        that does not match the configured token owner.
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
            "Cannot create GitHub repository: no github_token configured. Set GITHUB_TOKEN in the backend environment."
        )

    url = _resolve_repo_endpoint(owner, timeout=timeout)
    # auto_init=True asks GitHub to create an initial README and materialise
    # the default branch on the first commit. Without it the repo is
    # technically created but has no refs, which surprises the first
    # ``git clone`` (empty result) and a subsequent push would have to
    # invent the default branch locally. Initial README is trivial to
    # overwrite and is the common default across GitHub tooling.
    body = {
        "name": name,
        "description": description,
        "private": private,
        "auto_init": True,
    }

    response = httpx.post(url, headers=_github_headers(), json=body, timeout=timeout)

    if response.status_code == 201:
        logger.info("Created GitHub repository %s", repo)
        return True

    # GitHub returns 422 with an ``errors[].message`` of "name already exists"
    # when a repo with this name is already present. We treat that as
    # non-fatal — the caller wanted a repo, there is one.
    if response.status_code == 422:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        errors = payload.get("errors") or []
        already_exists = any(
            isinstance(e, dict) and "already exists" in str(e.get("message", "")).lower() for e in errors
        )
        if already_exists:
            logger.info("GitHub repository %s already exists — reusing", repo)
            return False
        raise RuntimeError(f"GitHub API rejected repository creation for '{repo}' (422): {response.text}")

    if response.status_code in (401, 403):
        raise RuntimeError(
            f"GitHub API refused repository creation for '{repo}' ({response.status_code}): "
            "token missing or insufficient scope (needs 'repo' / 'admin:org')."
        )

    raise RuntimeError(
        f"GitHub API returned unexpected status {response.status_code} "
        f"for repository creation '{repo}': {response.text}"
    )


def delete_github_repo(repo: str, *, timeout: float = _DEFAULT_GITHUB_API_TIMEOUT) -> bool:
    """Delete an existing GitHub repository.

    Opt-in counterpart to :func:`create_github_repo` — the
    ``DELETE /api/v1/projects/{id}?delete_github=true`` flow calls
    this after the DB row is gone. Without the query flag NEX Studio
    only cleans up DB + KB and leaves the repo untouched.

    Returns
    -------
    bool
        ``True`` when the repository was deleted (HTTP 204).
        ``False`` when the repository did not exist (HTTP 404) — the
        caller's goal was "be gone", and it already is.

    Raises
    ------
    ValueError
        If ``repo`` is not in ``owner/repo`` format.
    RuntimeError
        If no ``github_token`` is configured, the token lacks the
        ``delete_repo`` scope (HTTP 403), or the API returns an
        unexpected status.
    """
    if not repo or repo.count("/") != 1:
        raise ValueError(f"Invalid repository format '{repo}'. Expected 'owner/repo'.")

    owner, name = repo.split("/")
    if not owner or not name:
        raise ValueError(f"Invalid repository format '{repo}'. Owner and repo name must not be empty.")

    if not settings.github_token:
        raise RuntimeError("Cannot delete GitHub repository: no github_token configured.")

    url = f"{GITHUB_API_BASE}/repos/{owner}/{name}"
    response = httpx.delete(url, headers=_github_headers(), timeout=timeout)

    if response.status_code == 204:
        logger.info("Deleted GitHub repository %s", repo)
        return True

    if response.status_code == 404:
        logger.info("GitHub repository %s not found — treating as already deleted", repo)
        return False

    if response.status_code in (401, 403):
        raise RuntimeError(
            f"GitHub API refused repository deletion for '{repo}' ({response.status_code}): "
            "token missing or insufficient scope (needs 'delete_repo')."
        )

    raise RuntimeError(
        f"GitHub API returned unexpected status {response.status_code} "
        f"for repository deletion '{repo}': {response.text}"
    )
