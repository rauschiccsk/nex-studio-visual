"""The machines a customer's PROD may run on, and how each is named on screen (DEV-274).

Where the cockpit deploys PROD is ``customers.prod_host``: empty = this machine, otherwise the cockpit reaches the
machine with ``DOCKER_HOST=ssh://<name>``. On 31.10.2026 only MÁGERSTAV had it set — written straight into the
database — and the Director decided that ICC's production belongs on MAGER too, which nobody could set from the
cockpit. The list of machines lives in the settings (one place); this module only reads it.
"""

from __future__ import annotations

from typing import Optional

from backend.config.settings import settings


def choices() -> list[tuple[str, str]]:
    """``(value, label)`` for every machine PROD may run on — this machine first, as the empty value."""
    return [("", settings.this_machine_label), *settings.prod_machines.items()]


def label(prod_host: Optional[str]) -> str:
    """How the machine of ``prod_host`` is named on screen; an unknown one is shown by its name, never hidden."""
    if not prod_host:
        return settings.this_machine_label
    return settings.prod_machines.get(prod_host, prod_host)


def check(prod_host: Optional[str]) -> str:
    """The value to store — ``""`` for this machine. Raises ``ValueError`` for a machine the cockpit does not know."""
    value = (prod_host or "").strip()
    if value and value not in settings.prod_machines:
        known = ", ".join(lbl for _v, lbl in choices())
        raise ValueError(f"Ostrý stroj „{value}“ kokpit nepozná — na výber je: {known}.")
    return value
