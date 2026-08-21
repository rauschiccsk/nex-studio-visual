"""Reserved port ranges — the guard that stops the cockpit handing out a neighbour's block.

The setting-based path IS covered — ``tests/services/test_port_registry.py`` has
``test_reserved_port_is_taken`` and friends. What no test could catch is that the setting
was never FILLED: an empty reservation list is configuration, not code, so the suite stayed
green while ``reserved_ranges_status`` reported "no reservations" and the cockpit offered
10120-10122 to a new project — inside NEX Automat's reserved 10110-10159 (D-022). Nothing
failed; the suggestion simply looked plausible and would have collided the day NEX Automat
started.

These tests cover the file-based path that replaced it, and the last one asserts against
the REAL registry — so an empty or broken registry now fails the build instead of quietly
disarming the guard.

Since ICCINT-2 the source of record is the KB registry file, not the setting.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import port_registry


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the service at a registry we control, never the live KB file."""

    def _write(text: str) -> Path:
        path = tmp_path / "port-registry.yaml"
        path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", path)
        return path

    return _write


def test_external_blocks_are_reserved(registry) -> None:
    registry("bloky:\n  - rozsah: 10110-10159\n    vlastník: NEX Automat\n    druh: externý\n")
    ranges, malformed, found = port_registry._ranges_from_registry_file()
    assert found is True
    assert malformed == ()
    assert ranges == ((10110, 10159),)


def test_cockpit_owned_blocks_are_NOT_reserved(registry) -> None:
    """A cockpit block must never enter the reserved set.

    Reserved ranges are consulted after the projects table but do not know which project
    is asking. The projects lookup excludes the asking project, so reserving its own block
    would make the cockpit answer "reserved" when a project re-checks a port it already
    holds. Cockpit projects are protected by the projects table; this second guard would
    only ever be wrong.
    """
    registry(
        "bloky:\n"
        "  - rozsah: 10190-10199\n"
        "    vlastník: nex-productcatalogs\n"
        "    druh: kokpit\n"
        "  - rozsah: 10110-10159\n"
        "    vlastník: NEX Automat\n"
        "    druh: externý\n"
    )
    ranges, _, _ = port_registry._ranges_from_registry_file()
    ports = {p for start, end in ranges for p in range(start, end + 1)}
    assert 10120 in ports, "NEX Automat's block must be protected"
    assert 10190 not in ports, "our own project's block must stay allocatable to itself"


def test_out_of_range_blocks_are_reserved_too(registry) -> None:
    registry("bloky: []\nmimo_rozsahu:\n  - rozsah: 9100-9299\n    vlastník: interné aplikácie ICC\n")
    ranges, _, _ = port_registry._ranges_from_registry_file()
    assert ranges == ((9100, 9299),)


def test_malformed_entry_is_reported_not_skipped(registry) -> None:
    """A mistyped range must be visible. Skipping it in silence leaves the operator
    believing a guard exists over a range that is in fact wide open."""
    registry(
        "bloky:\n"
        "  - rozsah: 10110-10159\n"
        "    vlastník: NEX Automat\n"
        "    druh: externý\n"
        "  - rozsah: 10200\n"
        "    vlastník: preklep\n"
        "    druh: externý\n"
        "  - rozsah: 10300-10250\n"
        "    vlastník: naopak\n"
        "    druh: externý\n"
    )
    ranges, malformed, _ = port_registry._ranges_from_registry_file()
    assert ranges == ((10110, 10159),)
    assert len(malformed) == 2
    assert any("preklep" in entry for entry in malformed)
    assert any("naopak" in entry for entry in malformed)


def test_missing_file_reports_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No file means fall back to the setting — never "no reservations"."""
    monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", tmp_path / "absent.yaml")
    assert port_registry._ranges_from_registry_file() == ((), (), False)


def test_unreadable_file_reports_not_found(registry) -> None:
    """Broken YAML must not read as "nothing is reserved"."""
    registry("bloky: [ this is not: valid: yaml\n")
    _, _, found = port_registry._ranges_from_registry_file()
    assert found is False


def test_live_registry_protects_the_block_that_was_handed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression against the real KB file: the exact port the cockpit offered by mistake.

    Reads the live registry deliberately — if someone empties or breaks it, this fails.
    The conftest autouse fixture points every test at an absent registry; this one opts
    back in explicitly, so the opt-in is visible rather than a silent skip.
    """
    live = Path("/home/icc/knowledge/infrastructure/port-registry.yaml")
    if not live.exists():  # pragma: no cover — KB not mounted (CI without the KB volume)
        pytest.skip("KB registry not mounted in this environment")
    monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", live)
    ranges, malformed, found = port_registry._ranges_from_registry_file()
    assert found is True
    assert malformed == (), f"live registry has malformed entries: {malformed}"
    ports = {p for start, end in ranges for p in range(start, end + 1)}
    assert 10120 in ports, "10120 was offered to nex-productcatalogs; it belongs to NEX Automat"


# ── Zápis späť ────────────────────────────────────────────────────────────────
# Reading one registry stops the cockpit handing out a neighbour's block; writing
# back is what stops the registry going stale again.

_REGISTRY = """\
# hlavička s vysvetlením, ktorá musí prežiť
verzia: 1
aktualizované: 2026-01-01

rozsahy:
  komerčné:
    next_free: 10230          # udržiavať pri každom pridelení

bloky:
  # komentár vnútri zoznamu blokov
  - rozsah: 10110-10159
    vlastník: NEX Automat
    druh: externý

# ── Mimo komerčného rozsahu ──────────────────────────────────────────────
mimo_rozsahu:
  - rozsah: 9100-9299
    vlastník: interné aplikácie ICC
"""


@pytest.fixture
def writable_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "port-registry.yaml"
    path.write_text(_REGISTRY, encoding="utf-8")
    monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", path)
    return path


def _record(**kw):
    base = kw.pop("base", 10230)
    return port_registry.record_allocation(
        slug=kw.pop("slug", "novy-projekt"),
        base=base,
        block_size=10,
        backend_port=base,
        frontend_port=base + 1,
        db_port=base + 2,
        **kw,
    )


def test_allocation_is_recorded_and_next_free_advances(writable_registry: Path) -> None:
    assert _record() is None
    text = writable_registry.read_text(encoding="utf-8")
    assert "rozsah: 10230-10239" in text
    assert "vlastník: novy-projekt" in text
    assert "druh: kokpit" in text
    assert "next_free: 10240" in text


def test_comments_survive_the_write(writable_registry: Path) -> None:
    """The registry is a document a human maintains — round-tripping it through the YAML
    dumper would hand back a machine's idea of the same data and drop every explanation."""
    before = writable_registry.read_text(encoding="utf-8").count("#")
    _record()
    after = writable_registry.read_text(encoding="utf-8")
    assert after.count("#") == before
    assert "# hlavička s vysvetlením, ktorá musí prežiť" in after
    assert "# komentár vnútri zoznamu blokov" in after


def test_entry_lands_inside_bloky_not_in_the_next_section(writable_registry: Path) -> None:
    _record()
    text = writable_registry.read_text(encoding="utf-8")
    assert text.index("10230-10239") < text.index("mimo_rozsahu:")
    assert text.index("10230-10239") > text.index("bloky:")


def test_recording_twice_does_not_duplicate(writable_registry: Path) -> None:
    """Creates get retried. A second run must be a no-op, not a second entry."""
    _record()
    assert _record() is None
    assert writable_registry.read_text(encoding="utf-8").count("rozsah: 10230-10239") == 1


def test_written_file_is_still_valid_yaml(writable_registry: Path) -> None:
    import yaml

    _record()
    doc = yaml.safe_load(writable_registry.read_text(encoding="utf-8"))
    assert [b["rozsah"] for b in doc["bloky"]] == ["10110-10159", "10230-10239"]
    assert doc["rozsahy"]["komerčné"]["next_free"] == 10240


def test_missing_file_returns_a_warning_and_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A project that exists must never be rolled back because a file could not be written —
    but the Manažér has to be told, or the next project gets handed the same block."""
    monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", tmp_path / "absent.yaml")
    warning = _record()
    assert warning is not None
    assert "10230-10239" in warning
    assert "ručne" in warning


def test_registry_without_the_anchor_section_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "port-registry.yaml"
    path.write_text("bloky: []\n", encoding="utf-8")
    monkeypatch.setattr(port_registry, "PORT_REGISTRY_FILE", path)
    warning = _record()
    assert warning is not None
    assert "mimo_rozsahu" in warning


def test_project_without_ports_records_nothing_and_warns_about_nothing(
    writable_registry: Path,
) -> None:
    """``backend_port`` is nullable. No block means nothing to write down — and nothing to
    warn about either, or every portless project would nag the Manažér about a non-problem."""
    before = writable_registry.read_text(encoding="utf-8")
    warning = port_registry.record_allocation(
        slug="bez-portov",
        base=None,
        block_size=10,
        backend_port=None,
        frontend_port=None,
        db_port=None,
    )
    assert warning is None
    assert writable_registry.read_text(encoding="utf-8") == before
