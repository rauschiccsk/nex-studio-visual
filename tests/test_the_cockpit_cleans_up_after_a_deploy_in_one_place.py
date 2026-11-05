"""DEV-305 — what a deploy leaves on a machine is cleaned in one place, not in two that disagree.

05.11.2026: the room check of DEV-305 came with a cleanup of its own — older versions of an installation's images and
the build cache under a cap of 10 GB — while the cockpit already had both: DEV-124 (``orchestrator._uprac_stare_verzie``
keeps the deployed version, every newer one and the newest older one) and ICCINT-154 / DEV-166
(``orchestrator._uprac_na_cieli`` keeps the build cache under ``settings.deploy_build_cache_max_gb`` on another machine
and leaves it on ANDROS, where the tests need it). The second cleanup kept other versions and would have cut ANDROS's
build cache from 92 GB to 10 GB on the next UAT deploy. It went before any deploy ran it. The rule: one module removes
images and prunes the build cache; a new need goes there.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"

#: A docker call that throws away what a deploy left: images, their layers, the build cache.
CLEANUP = re.compile(r'"builder",\s*"prune"|"image",\s*"(?:rm|prune)"|"rmi"|"system",\s*"prune"')

#: The one place the cockpit cleans a machine up after a deploy (DEV-124, ICCINT-154 / DEV-166).
THE_CLEANER = "services/orchestrator.py"


def _cleaners() -> set[str]:
    return {
        str(path.relative_to(BACKEND))
        for path in BACKEND.rglob("*.py")
        if "tests" not in path.parts and CLEANUP.search(path.read_text(encoding="utf-8"))
    }


def test_images_and_the_build_cache_are_thrown_away_in_one_module():
    assert _cleaners() == {THE_CLEANER}


def test_the_rule_sees_each_way_of_cleaning_up():
    """A pattern that matches nothing is green in a burning house: every form of the calls it guards is recognised."""
    for call in (
        '["docker", "builder", "prune", "-af"]',
        '["docker", "image", "rm", ref]',
        '["docker", "image", "prune", "-f"]',
        '["rmi", reference]',
        '["docker", "system", "prune", "-f"]',
    ):
        assert CLEANUP.search(call), call
    assert not CLEANUP.search('["docker", "rm", "-f", name]'), "removing a container is not cleaning up images"
