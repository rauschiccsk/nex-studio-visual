"""DEV-46 — the cockpit ships skills to every build agent, in the one shape Claude Code loads.

A skill is a procedure an agent needs only sometimes: at start Claude Code shows the agent each skill's description
and loads the body when the agent uses it. The charter names the skill in one sentence and the procedure stops
costing every turn. Until DEV-46 the project template copied two skills as loose ``.claude/skills/<name>.md``
files — measured 09.10.2026 on Claude Code 2.1.294 with the cockpit's own ``--setting-sources user,project``: a
skill is loaded only as ``.claude/skills/<name>/SKILL.md``, so no build agent ever had them.

Director 09.10.2026: „u teba sme robili tzv. rules a skills, tu nepotrebujeme ich mať? Nepomohli by nám aj tu?“ →
„Áno, založ tiket do DEV“; 10.10.2026: „Áno, začni DEV-46“.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from backend.services import create_project_postscaffold as cps

#: How the instructions name a skill: „zručnosť `backend-tests`“, „podľa zručnosti `tdd`“.
_NAMED_SKILL = re.compile(r"zručnos\w*\s+`([a-z0-9-]+)`")


def _front_matter(text: str) -> dict:
    assert text.startswith("---\n"), "zručnosť nezačína hlavičkou (---)"
    front, end, _body = text[len("---\n") :].partition("\n---\n")
    assert end, "hlavička zručnosti nie je uzavretá (---)"
    return yaml.safe_load(front)


def _scaffold(tmp_path: Path) -> Path:
    root = tmp_path / "projekt"
    (root / ".claude").mkdir(parents=True)
    return root


def _project_skills(root: Path) -> dict[str, str]:
    skills_dir = root / ".claude" / "skills"
    return {
        entry.name: (entry / cps.SKILL_FILE).read_text(encoding="utf-8")
        for entry in sorted(skills_dir.iterdir())
        if entry.is_dir()
    }


def test_every_shipped_skill_has_the_shape_claude_code_loads():
    entries = sorted(cps.SKILLS_TEMPLATE_DIR.iterdir())

    assert entries, "kokpit nedodáva ani jednu zručnosť — šablóny templates/skills/ chýbajú"
    for entry in entries:
        assert entry.is_dir(), f"{entry.name}: voľný súbor Claude Code nenačíta — zručnosť je priečinok so SKILL.md"
        template = entry / cps.SKILL_FILE
        assert template.is_file(), f"{entry.name}: chýba {cps.SKILL_FILE}"
        front = _front_matter(template.read_text(encoding="utf-8"))
        assert front.get("name") == entry.name, f"{entry.name}: meno v hlavičke sa nezhoduje s priečinkom"
        assert str(front.get("description") or "").strip(), f"{entry.name}: bez opisu agent nevie, kedy ju použiť"


def test_a_new_project_gets_every_skill_in_loadable_form(tmp_path):
    root = _scaffold(tmp_path)
    old_template = root / ".claude" / "skills"
    old_template.mkdir()
    for loose in ("tdd.md", "systematic-debugging.md"):
        (old_template / loose).write_text("---\nname: x\ndescription: y\n---\n", encoding="utf-8")

    cps.provision_v2_agent_charters(root, "projekt", "Projekt", adopted=False)

    assert _project_skills(root) == cps.cockpit_skills()
    leftovers = [p.name for p in (root / ".claude" / "skills").iterdir() if p.is_file()]
    assert leftovers == [], f"voľné súbory starej šablóny ostali v projekte: {leftovers}"
    for name, text in _project_skills(root).items():
        assert _front_matter(text)["name"] == name, f"{name}: značka kokpitu pokazila hlavičku"
        assert cps.COCKPIT_SKILL_MARKER in text, f"{name}: bez značky ju obnova nespozná ako zručnosť kokpitu"


def test_refresh_brings_the_skills_up_to_date_and_retires_only_its_own(tmp_path):
    root = _scaffold(tmp_path)
    cps.provision_v2_agent_charters(root, "projekt", "Projekt", adopted=False)
    skills_dir = root / ".claude" / "skills"
    stale = sorted(cps.cockpit_skills())[0]
    (skills_dir / stale / cps.SKILL_FILE).write_text("stará verzia", encoding="utf-8")
    (skills_dir / "retired").mkdir()
    (skills_dir / "retired" / cps.SKILL_FILE).write_text(
        f"---\nname: retired\ndescription: zrušená\n---\n\n{cps.COCKPIT_SKILL_MARKER}\n", encoding="utf-8"
    )
    (skills_dir / "projects-own").mkdir()
    own = "---\nname: projects-own\ndescription: vlastná zručnosť projektu\n---\n"
    (skills_dir / "projects-own" / cps.SKILL_FILE).write_text(own, encoding="utf-8")
    (skills_dir / "tdd.md").write_text("voľný súbor starej šablóny", encoding="utf-8")

    cps.refresh_v2_agent_charters(root, "projekt")

    assert _project_skills(root) == {**cps.cockpit_skills(), "projects-own": own}
    assert not (skills_dir / "tdd.md").exists(), "voľný súbor starej šablóny prežil obnovu"


def test_every_skill_the_instructions_name_is_shipped_and_every_shipped_skill_is_named():
    texts = [
        (cps.NEX_STUDIO_TEMPLATES / name).read_text(encoding="utf-8")
        for name in (
            "agent-shared-base.md",
            "ai-agent-charter.md",
            "auditor-charter.md",
            "project-claude-md.md",
        )
    ]
    shipped = cps.cockpit_skills()
    named_by_charters = {name for text in texts for name in _NAMED_SKILL.findall(text)}
    named_by_skills = {name for text in shipped.values() for name in _NAMED_SKILL.findall(text)}

    assert named_by_charters - set(shipped) == set(), "pravidlá odkazujú na zručnosť, ktorú kokpit nedodáva"
    assert named_by_skills - set(shipped) == set(), "zručnosť odkazuje na zručnosť, ktorú kokpit nedodáva"
    assert set(shipped) - named_by_charters == set(), (
        "kokpit dodáva zručnosť, na ktorú charta neodkazuje — agent sa o nej dozvie len náhodou z opisu"
    )


def test_a_skill_write_failure_stops_founding_but_not_a_build(tmp_path, monkeypatch):
    root = _scaffold(tmp_path)
    cps.provision_v2_agent_charters(root, "projekt", "Projekt", adopted=False)

    def _disk_full(_claude_dir):
        raise OSError("No space left on device")

    with monkeypatch.context() as m:
        m.setattr(cps, "_sync_skills", _disk_full)
        with pytest.raises(cps.ProvisioningError, match="skills"):
            cps.provision_v2_agent_charters(root, "projekt", "Projekt", adopted=False)
        cps.refresh_v2_agent_charters(root, "projekt")  # a build is never stopped by a refresh
