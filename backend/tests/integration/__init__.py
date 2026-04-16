"""Integration tests — end-to-end coverage of BEHAVIOR.md workflows.

Each module exercises a single workflow from BEHAVIOR.md §3 against the
real FastAPI ``app`` (mounted via ``backend.main.app``), driven through
the HTTP layer with the SAVEPOINT-isolated test database provided by
``tests.conftest``. Unlike the per-router unit tests in ``tests/``, which
mount a single router on a throwaway ``FastAPI()`` app, these tests hit
the full application — routers, services, ORM models and DB CHECK
constraints — to verify each workflow's **Precondition**, **Steps**,
**Postcondition** and at least one **Edge case** end-to-end.
"""
