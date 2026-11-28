"""DEV-536 — Sprievodca in an app on the template: whether it is on, and the switch that turns it off for a special app.

Sprievodca (nex-app-kit 0.24.0+, „Ako urobiť“ and the guiding step by step) is on in every app on the template unless
its manifest says ``"guide": false``. The manifest is the one place it is kept — the template reads it at start, this
switch writes it and commits it; the cockpit keeps no copy. Director 28.11.2026: „Má byť default, že tam je ale
v prípade, že ide o nejakú špeciálnu aplikáciu aby sme to mohli vypnúť.“"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.db.models.projects import Project
from backend.services import app_languages, git_state
from backend.services import create_project_postscaffold as postscaffold

logger = logging.getLogger(__name__)

#: The release of nex-app-kit that brings Sprievodca.
GUIDE_SINCE = "0.24.0"
#: The manifest key; absent or ``true`` = on.
GUIDE_KEY = "guide"
MANIFEST = app_languages.MANIFEST
#: Where an app keeps its catalog of steps (the template's scaffold since 0.24.1) — read by the check before Overenie.
CATALOG_FILE = "frontend/src/guide.ts"
COMMIT_ON = "chore(guide): Sprievodca on (the cockpit's switch)"
COMMIT_OFF = "chore(guide): Sprievodca off (the cockpit's switch)"


class GuideSwitchError(RuntimeError):
    """Why the switch could not be turned — the Slovak sentence the Manažér reads."""


@dataclass(frozen=True)
class GuideSwitch:
    #: The project is on the app template — elsewhere there is no Sprievodca to switch.
    applies: bool
    on: bool
    #: Why it cannot be switched now (an older template, an unreadable manifest, unsaved work in it); ``None`` = can.
    refusal: Optional[str]
    kit_version: Optional[str]


def is_on(manifest: dict) -> bool:
    """Sprievodca is on unless the manifest says ``false`` — the template's own rule."""
    return manifest.get(GUIDE_KEY) is not False


def kit_knows_guide(version: Optional[str]) -> bool:
    """Whether the release *version* of nex-app-kit has Sprievodca."""
    if not version:
        return False
    try:
        return app_languages._key(version) >= app_languages._key(GUIDE_SINCE)
    except ValueError:
        return False


def read_manifest(root: Path) -> dict:
    """The app's manifest — raises ``OSError``/``ValueError`` when it cannot be read."""
    data = json.loads((Path(root) / MANIFEST).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("the manifest is no JSON object")
    return data


def guide_switch(project: Project) -> GuideSwitch:
    """Whether *project*'s app has Sprievodca on, and whether it can be switched now — nothing changes."""
    if not project.template or not project.source_path:
        return GuideSwitch(applies=False, on=False, refusal=None, kit_version=None)
    root = Path(project.source_path)
    try:
        manifest = read_manifest(root)
    except (OSError, ValueError):
        return GuideSwitch(
            applies=True,
            on=False,
            refusal=f"Manifest aplikácie ({MANIFEST}) sa nedá prečítať — Sprievodcu teraz nemožno zapnúť ani vypnúť.",
            kit_version=None,
        )
    version = app_languages.kit_version(str(root))
    on = is_on(manifest)
    if not kit_knows_guide(version):
        return GuideSwitch(
            applies=True,
            on=False,
            refusal=(
                f"Sprievodcu pozná šablóna od verzie nex-app-kit {GUIDE_SINCE}, projekt stojí na "
                f"{version or 'verzii, ktorá sa nedá prečítať'}. Najprv povýš šablónu aplikácie."
            ),
            kit_version=version,
        )
    unsaved = _unsaved(project)
    return GuideSwitch(applies=True, on=on, refusal=unsaved, kit_version=version)


def set_guide(project: Project, on: bool) -> tuple[GuideSwitch, list[str]]:
    """Turn Sprievodca of *project*'s app on or off: write the manifest and commit it. Returns the switch as it is now
    and the Slovak sentences of what finished only partly (a push that did not go through). Raises
    :class:`GuideSwitchError` when the switch cannot be turned now."""
    before = guide_switch(project)
    if not before.applies:
        raise GuideSwitchError("Projekt nie je na šablóne aplikácie — Sprievodcu nemá.")
    if before.refusal:
        raise GuideSwitchError(before.refusal)
    if before.on == on:
        return before, []
    root = Path(project.source_path or "")
    manifest = read_manifest(root)
    # On is the template's default — the key is left out rather than written, so the manifest says only an exception.
    if on:
        manifest.pop(GUIDE_KEY, None)
    else:
        manifest[GUIDE_KEY] = False
    (root / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # scaffold=True — one key the template validates at start; the pre-commit check would type-check the frontend
    # with installed packages a workspace need not have, and refuse a change that touches no code.
    note = postscaffold.commit_and_push(root, [MANIFEST], COMMIT_ON if on else COMMIT_OFF, project.slug, scaffold=True)
    logger.info("Sprievodca of slug=%s switched %s", project.slug, "on" if on else "off")
    return guide_switch(project), [note] if note else []


def _unsaved(project: Project) -> Optional[str]:
    """Unsaved work in the manifest the switch commits — it would ride along in the switch's commit unseen."""
    try:
        tree = git_state.working_tree_status(project.source_path or "", paths=[MANIFEST])
    except ValueError:
        return "Priečinok projektu nie je repozitár gitu — zmenu Sprievodcu by nebolo kam uložiť."
    if tree["clean"]:
        return None
    return f"V manifeste aplikácie ({MANIFEST}) sú neuložené zmeny — Sprievodcu prepni, keď ich Staviteľ uloží."
