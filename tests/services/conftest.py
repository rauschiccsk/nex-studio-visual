"""Shared fixtures pre tests/services/ — backend services unit tests.

Per CR-030 cleanup batch (2026-05-26) — Návrh #1: caplog autouse fixture
shared cez conftest, namiesto per-test duplikácie.
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _enable_log_propagation():
    """Re-enable `backend.*` logger propagation pre `caplog` v service tests.

    `backend/main.py:71` sets `propagate=False` na `backend` logger (production
    handler routes logs cez explicit stderr formatter). For pytest `caplog`
    fixture to capture log records emitted from `backend.services.*` (alebo
    other `backend.*` submodules), propagation must be temporarily re-enabled.

    Autouse scope: tests/services/ only — žiadny side-effect na production
    flow ani integration tests inde v repo (existing semantics zachované).
    """
    backend_logger = logging.getLogger("backend")
    original = backend_logger.propagate
    backend_logger.propagate = True
    try:
        yield
    finally:
        backend_logger.propagate = original
