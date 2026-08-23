"""The bind-mount path guards EVERY project sandbox shares — one copy, on purpose.

WHY ONE MODULE AND NOT A COPY PER SANDBOX. Three sandboxes now compose a ``docker run`` bind whose SOURCE
is derived from a project slug: the read-only Konzultácia sidecar (:mod:`consult_sandbox`), the live Vite
preview (:mod:`vizual_sandbox`) and the build sandbox (:mod:`build_sandbox`). All three depend on the same
two invariants, and both are load-bearing rather than cosmetic:

  * **the slug is canonical** — ``pathlib`` does NOT normalise ``..``, so an unvalidated slug of ``..``
    composes the bind source ``<root>/..`` and docker mounts ALL of ``/opt`` (every customer, every UAT
    deployment, every other project) into the sandbox. The mount IS the guarantee, so a slug that can
    widen it is the whole guarantee;
  * **the resolved source stays under an intended root** — symlink-followed, independently of the slug
    check, so a future prefix change or a symlink planted in the projects tree cannot silently broaden a
    mount that the slug rule happily accepted.

Two copies of a security check drift, and the drift is invisible: the copy that stops being correct keeps
passing its own tests. So the rule lives here once and each sandbox supplies only what genuinely differs —
its own root prefixes and its own error label — while the logic being trusted is a single body of code.

The functions raise :class:`SandboxPathError` (a ``ValueError``); each caller translates it into whatever
its own callers already catch, so no existing contract changes.
"""

from __future__ import annotations

import os

#: The ICC-canonical kebab-case project-slug rule, reused verbatim (DRY) so every sandbox accepts EXACTLY
#: the slugs the project system considers valid. Rejects ``..`` / ``/`` / empty / anything non-slug.
from backend.services.project_specs import _SLUG_RE as _PROJECT_SLUG_RE

#: ``((in-container prefix, host prefix), …)`` — how a sandbox translates the backend's own view of a
#: project directory into the path the HOST docker daemon must resolve for a ``--mount`` source. A sibling
#: ``docker run`` is executed by the daemon on the host, so the source is a HOST path, never the backend's
#: in-container view; where a directory is bind-mounted identically the two are simply equal.
HostPrefixes = tuple[tuple[str, str], ...]


class SandboxPathError(ValueError):
    """A slug or path that must never be composed into (or left inside) a bind mount."""


def validate_project_slug(project_slug: object, *, sandbox: str) -> None:
    """Reject any non-canonical project slug BEFORE it is composed into a bind source.

    ``sandbox`` is the caller's own label, so the raised message reads in that sandbox's register
    (e.g. ``"consult sidecar: refusing unsafe project slug '..'"``).
    """
    if not isinstance(project_slug, str) or not _PROJECT_SLUG_RE.match(project_slug):
        raise SandboxPathError(f"{sandbox}: refusing unsafe project slug {project_slug!r}")


def container_to_host(container_dir: str, prefixes: HostPrefixes, *, sandbox: str) -> str:
    """Translate an in-container project dir → the HOST path used as the ``--mount`` source.

    An unmapped path raises rather than being passed through: a wrong source is not a cosmetic problem —
    docker would either refuse it (``--mount``) or, far worse, mount something unintended.
    """
    for container_prefix, host_prefix in prefixes:
        if container_dir == container_prefix or container_dir.startswith(container_prefix + "/"):
            return host_prefix + container_dir[len(container_prefix) :]
    raise SandboxPathError(f"{sandbox}: cannot map in-container project path {container_dir!r} to a host path")


def assert_host_source_contained(host_dir: str, prefixes: HostPrefixes, *, sandbox: str) -> None:
    """Belt-and-suspenders: the RESOLVED (symlink-followed) bind source must stay strictly UNDER a root.

    Deliberately independent of :func:`validate_project_slug` — two orthogonal layers guarding the same
    invariant, so neither one being wrong is enough to widen a mount. ``strictly under`` matters: the bare
    root itself is refused, because mounting the root mounts every project at once.
    """
    real = os.path.realpath(host_dir)
    for _container_prefix, host_prefix in prefixes:
        if real.startswith(os.path.realpath(host_prefix) + os.sep):
            return
    raise SandboxPathError(f"{sandbox}: refusing bind source {host_dir!r} — resolves outside the project roots")
