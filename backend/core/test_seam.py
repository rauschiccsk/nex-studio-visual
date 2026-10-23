"""DEV-155 — the one mark of code that exists for the tests.

A reset of a module's memory between tests, a hook that reaches inside a registry: production never calls it, and
the dead-code guard (``tests/test_no_dead_code.py``) lets it be because the definition says so — not because a list
somewhere names it. Anything else production does not call is dead and goes.
"""

from __future__ import annotations

from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable)


def for_tests(fn: F) -> F:
    """Mark ``fn`` as existing for the tests; it changes nothing at run time."""
    return fn
