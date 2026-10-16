"""Does a NEX Manager agree with the modules launched from it on what a launch ticket looks like (DEV-69)?

11.10.2026 the UAT NEX Manager of MAGERSTAV signed its tickets with the installation name „local“ (the default of its
own ``.env.example``) while the UAT NEX Inbox accepted only „uat-mager“ — the names both installations had before the
cockpit took them over. Every launch from the Manager ended in NIB-081, and nothing in the cockpit said so: the deploy
door check (ICCINT-69) minted its test ticket from the MODULE's own settings, i.e. from the very values the module
verifies against, so it could not fail.

A ticket the Manager signs carries the Manager's key, its ``DEPLOY_SLUG`` and the module's slug as the Manager knows
it — the one the cockpit registers (:func:`uat_provisioner.ensure_module_registered`, the project slug). The module
checks the key, ``MANAGER_DEPLOY_SLUG`` (an empty one switches that check off) and ``MANAGER_MODULE_SLUG``. This
module compares the two sides without ever showing a key.
"""

from __future__ import annotations

from typing import Mapping

#: The NEX Manager's own settings that shape every ticket it signs.
MANAGER_KEY, MANAGER_DEPLOY = "LAUNCH_SIGNING_KEY", "DEPLOY_SLUG"
#: What a module launched from the Manager verifies a ticket against.
MODULE_KEY, MODULE_DEPLOY, MODULE_SLUG = "MANAGER_LAUNCH_SIGNING_KEY", "MANAGER_DEPLOY_SLUG", "MANAGER_MODULE_SLUG"
#: The project of the NEX Manager itself.
MANAGER_PROJECT = "nex-manager"


def is_launched_from_manager(module_env: Mapping[str, str]) -> bool:
    """A module takes tickets from the Manager only when it has a key to check them with."""
    return bool(module_env.get(MODULE_KEY))


def mismatches(manager_env: Mapping[str, str], module_env: Mapping[str, str], registered_slug: str) -> list[str]:
    """What a ticket the Manager signs would fail on in the module — one sentence part each; ``[]`` = they agree."""
    found: list[str] = []
    manager_key, module_key = manager_env.get(MANAGER_KEY) or "", module_env.get(MODULE_KEY) or ""
    if not manager_key:
        found.append("NEX Manager nemá podpisový kľúč, takže lístok nepodpíše")
    elif manager_key != module_key:
        found.append("podpisový kľúč modulu sa nezhoduje s kľúčom NEX Managera")
    module_deploy = module_env.get(MODULE_DEPLOY) or ""
    manager_deploy = manager_env.get(MANAGER_DEPLOY) or ""
    if module_deploy and module_deploy != manager_deploy:
        signed_as = manager_deploy or "žiadnym"
        found.append(f"NEX Manager podpisuje menom inštalácie „{signed_as}“, modul prijíma len „{module_deploy}“")
    module_slug = module_env.get(MODULE_SLUG) or ""
    if module_slug and module_slug != registered_slug:
        found.append(f"NEX Manager otvára modul ako „{registered_slug}“, modul čaká „{module_slug}“")
    return found


def customer_warning(manager_env: Mapping[str, str], module_envs: Mapping[str, Mapping[str, str]]) -> str | None:
    """One sentence for the manager about every module of one customer's environment the Manager could not open."""
    broken = [
        f"{slug}: {'; '.join(found)}"
        for slug, env in sorted(module_envs.items())
        if is_launched_from_manager(env) and (found := mismatches(manager_env, env, slug))
    ]
    if not broken:
        return None
    return (
        "Z NEX Managera sa nedá otvoriť: " + " | ".join(broken) + ". Aplikácie bežia — pokazené je otvorenie z NEX "
        "Managera; nastavenia treba zladiť (urobí to technický tím)."
    )
