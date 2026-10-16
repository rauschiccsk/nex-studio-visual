"""The cockpit's door to the Knowledge Base's git (DEV-7, DEV-94).

What the cockpit writes into the Knowledge Base — an approved database schema, a port block, a deleted project's
folder — is committed with ONLY those paths, on a private index built from the committed versions, so somebody's
unfinished work stays in the working tree exactly as it was. The commit then goes to GitHub, where the copy on MAGER
reads it from (12.10.2026 the approved schema of NEX Inbox 1.8.0 lay committed on ANDROS and never left it).

Publishing never overwrites and never merges: the working tree is shared with people and other agents, and a merge
would reach into their unfinished work. When GitHub holds commits this tree lacks, that is said and Andros Dedo
reconciles it by hand (Director 13.10.2026). A failed push leaves the commit here; the next write and the loop in
``backend.main`` send it again.

Git runs as the repository's owner, so the root-run backend leaves no file only root can touch. GitHub is reached
over HTTPS — the backend container has no ssh — with the token handed to git in the environment, never in a command
line another process could read.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

from backend.config.settings import settings
from backend.services import kb_owner

#: Local git work (read, hash, commit) — never the network.
_LOCAL_TIMEOUT_SECONDS = 30

#: The identity the cockpit commits under (the same one the backend image configures for project repositories).
AUTHOR = ("NEX Studio", "studio@isnex.eu")

_SSH_GITHUB = re.compile(r"^(?:ssh://)?git@github\.com[:/](?P<path>.+?)(?:\.git)?/?$")

#: Said when GitHub holds commits this Knowledge Base lacks — nothing is merged in the shared working tree.
GITHUB_AHEAD = (
    "GitHub má v Znalostnej báze commity, ktoré na ANDROSe nie sú — kokpit ich nezlučuje, lebo v priečinku Znalostnej "
    "bázy môže ležať cudzia rozrobená práca. Zladí ich Andros Dedo; zápis zatiaľ ostáva len na ANDROSe."
)


#: Said after a failure the loop in ``backend.main`` gets past on its own — not after one only a person can resolve.
_SENT_AGAIN = " Kokpit to skúsi odoslať znova sám."


class Refused(Exception):
    """The Knowledge Base was not written — the message says why, in words the manager can act on."""


class Moved(Refused):
    """The branch moved between reading and writing — somebody committed meanwhile; nothing was thrown away."""


def is_repo(root: Path) -> bool:
    return (Path(root) / ".git").exists()


def repo_of(path: Path) -> Optional[Path]:
    """The Knowledge Base repository ``path`` lies in, or ``None`` when it lies in none."""
    for candidate in [Path(path), *Path(path).parents]:
        if is_repo(candidate):
            return candidate
    return None


def _run(
    root: Path, args: list[str], *, stdin: Optional[str] = None, env: Optional[dict[str, str]] = None, timeout: int
) -> subprocess.CompletedProcess[str]:
    uid, gid = kb_owner.owner(root)
    as_owner = os.geteuid() == 0 and uid != 0
    run_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": tempfile.gettempdir(),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": AUTHOR[0],
        "GIT_AUTHOR_EMAIL": AUTHOR[1],
        "GIT_COMMITTER_NAME": AUTHOR[0],
        "GIT_COMMITTER_EMAIL": AUTHOR[1],
        **(env or {}),
    }
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
            user=uid if as_owner else None,
            group=gid if as_owner else None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refused(f"Git v Znalostnej báze neodpovedal ({exc.__class__.__name__}).") from exc


def git(root: Path, *args: str, stdin: Optional[str] = None, env: Optional[dict[str, str]] = None) -> str:
    """Run a local git command in the Knowledge Base as its owner; :class:`Refused` when git says no."""
    result = _run(root, list(args), stdin=stdin, env=env, timeout=_LOCAL_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise Refused(f"Git v Znalostnej báze odmietol príkaz: {result.stderr.strip()[:300]}")
    return result.stdout


def committed(root: Path, rel: str) -> Optional[str]:
    """``rel`` as HEAD holds it, or ``None`` when HEAD does not hold it."""
    listed = git(root, "ls-tree", "--name-only", "HEAD", "--", rel).strip()
    return git(root, "show", f"HEAD:{rel}") if listed == rel else None


def tracked_under(root: Path, prefix: str) -> list[str]:
    """Every file HEAD holds under the folder ``prefix``."""
    return git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", prefix.rstrip("/") + "/").split()


def commit_files(root: Path, files: Mapping[str, Optional[str]], message: str) -> Optional[str]:
    """Commit ``files`` (path → new text, ``None`` removes it) on top of HEAD through a private index.

    Only these paths change and the working tree is not touched, so nobody's unsaved work is ever carried along;
    a path somebody has staged is refused rather than committed over. Returns the new commit, or ``None`` when HEAD
    already holds exactly this. Raises :class:`Moved` when the branch moved meanwhile — a commit somebody made is
    never thrown away."""
    staged = git(root, "diff", "--cached", "--name-only", "--", *files).split()
    if staged:
        raise Refused(
            f"V Znalostnej báze sú pripravené na uloženie cudzie zmeny v {', '.join(staged)} — kokpit ich "
            "nepribalí ani neprepíše. Treba ich najprv uložiť alebo vrátiť."
        )
    branch = git(root, "symbolic-ref", "-q", "HEAD").strip()
    old = git(root, "rev-parse", "HEAD").strip()
    blobs = {
        rel: git(root, "hash-object", "-w", "--stdin", stdin=text).strip()
        for rel, text in files.items()
        if text is not None
    }
    removed = [rel for rel, text in files.items() if text is None]

    def _apply(env: Optional[dict[str, str]] = None) -> None:
        for rel in removed:
            git(root, "update-index", "--force-remove", "--", rel, env=env)
        for rel, blob in blobs.items():
            git(root, "update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}", env=env)

    with tempfile.TemporaryDirectory() as tmp:
        private = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if os.geteuid() == 0:
            uid, gid = kb_owner.owner(root)
            os.chown(tmp, uid, gid)
        git(root, "read-tree", "HEAD", env=private)
        _apply(private)
        tree = git(root, "write-tree", env=private).strip()
    if tree == git(root, "rev-parse", "HEAD^{tree}").strip():
        return None
    new = git(root, "commit-tree", tree, "-p", old, "-m", message).strip()
    try:
        git(root, "update-ref", "-m", message, branch, new, old)
    except Refused as exc:
        raise Moved("Znalostná báza sa práve menila — skús to ešte raz.") from exc
    _apply()  # the shared index follows for exactly these paths, so they do not show as changed
    return new


def https_url(url: str) -> str:
    """GitHub over HTTPS for an ssh remote — the backend container has no ssh; any other remote as it is."""
    found = _SSH_GITHUB.match(url.strip())
    return f"https://github.com/{found.group('path')}.git" if found else url.strip()


def network_env(url: str) -> dict[str, str]:
    """What git needs to talk to ``url``: no prompts, and for GitHub the token through a helper that reads it from the
    environment — the token is in no command line and no configuration value."""
    env = {"GIT_TERMINAL_PROMPT": "0"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or settings.github_token
    if url.startswith("https://github.com/") and token:
        env.update(
            {
                "NEX_KB_GITHUB_TOKEN": token,
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "credential.helper",
                "GIT_CONFIG_VALUE_0": "",
                "GIT_CONFIG_KEY_1": "credential.helper",
                "GIT_CONFIG_VALUE_1": (
                    '!f() { echo username=x-access-token; echo "password=$NEX_KB_GITHUB_TOKEN"; }; f'
                ),
            }
        )
    return env


def _network(root: Path, args: list[str], url: str) -> subprocess.CompletedProcess[str]:
    return _run(root, args, env=network_env(url), timeout=settings.kb_publish_timeout_seconds)


def _first_line(text: str) -> str:
    return (text.strip().splitlines() or [""])[0][:300].rstrip(".")


def publish(root: Path) -> Optional[str]:
    """Send this Knowledge Base's branch to GitHub. ``None`` when GitHub holds it; otherwise why not, in words.

    Never force, never merge: GitHub ahead of this tree is reported (:data:`GITHUB_AHEAD`)."""
    root = Path(root)
    if not is_repo(root):
        return "Znalostná báza nie je git úložisko — na GitHub sa nedá odoslať."
    try:
        origin = _run(root, ["remote", "get-url", "origin"], timeout=_LOCAL_TIMEOUT_SECONDS)
        if origin.returncode != 0:
            return "Znalostná báza nemá vzdialené úložisko (origin) — na GitHub sa nedá odoslať."
        url = https_url(origin.stdout)
        branch = git(root, "symbolic-ref", "--short", "-q", "HEAD").strip()
        head = git(root, "rev-parse", "HEAD").strip()
        listed = _network(root, ["ls-remote", url, f"refs/heads/{branch}"], url)
        if listed.returncode != 0:
            return (
                f"Odoslanie Znalostnej bázy na GitHub zlyhalo — GitHub neodpovedal: {_first_line(listed.stderr)}."
                + _SENT_AGAIN
            )
        remote = (listed.stdout.split() or [None])[0]
        if remote == head:
            return None
        if remote is not None:
            ahead = _run(root, ["merge-base", "--is-ancestor", remote, head], timeout=_LOCAL_TIMEOUT_SECONDS)
            if ahead.returncode != 0:
                return GITHUB_AHEAD
        pushed = _network(root, ["push", url, f"HEAD:refs/heads/{branch}"], url)
        if pushed.returncode != 0:
            return f"Odoslanie Znalostnej bázy na GitHub zlyhalo: {_first_line(pushed.stderr)}." + _SENT_AGAIN
    except Refused as exc:
        return f"Odoslanie Znalostnej bázy na GitHub zlyhalo: {str(exc).rstrip('.')}." + _SENT_AGAIN
    return None


def record(root: Path, files: Mapping[str, Optional[str]], message: str) -> Optional[str]:
    """Commit ``files`` (:func:`commit_files`) and send the Knowledge Base to GitHub (:func:`publish`).

    ``None`` when GitHub holds them; otherwise why not, in words — the write itself has already happened."""
    if not is_repo(root):
        return "Znalostná báza nie je git úložisko — zápis sa nedá uložiť s históriou ani odoslať na GitHub."
    try:
        commit_files(root, files, message)
    except Refused as exc:
        return f"Zápis sa neuložil do gitu Znalostnej bázy: {exc}"
    return publish(root)
