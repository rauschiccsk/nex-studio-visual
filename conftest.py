"""Repo-root conftest — applies to BOTH ``tests/`` and ``backend/tests/``.

``pyproject.toml`` sets ``testpaths = ["tests", "backend/tests"]`` and pytest walks up
from each test file for conftest.py, so a fixture defined in either suite's own conftest
is invisible to the other. Anything that must hold for the whole run belongs here.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_port_registry(tmp_path_factory, monkeypatch):
    """Keep the suite off the LIVE KB port registry.

    ``reserved_ranges_status`` reads ``/home/icc/knowledge/infrastructure/port-registry.yaml``
    (ICCINT-2). Left alone, tests depend on which blocks ICC has allocated in real life —
    the registry reserved 10110-10159 for NEX Automat and 10180-10189 for NEX Asistent, and
    a dozen tests that had picked ports in those ranges turned red for a reason that had
    nothing to do with what they were testing. Worse, they would turn red again on any
    future allocation, at a moment unrelated to the change that triggered it.

    Pointing at an absent file makes the service fall back to the ``reserved_port_ranges``
    setting, which is what the existing suite already controls per-test.

    The one test that WANTS the real file (``backend/tests/test_port_registry_reservations``
    ::test_live_registry_protects_the_block_that_was_handed_out) sets the attribute back
    explicitly, so the opt-in is visible rather than a silent skip.
    """
    from backend.services import port_registry

    absent = tmp_path_factory.mktemp("no-registry") / "port-registry.yaml"
    monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", absent)
