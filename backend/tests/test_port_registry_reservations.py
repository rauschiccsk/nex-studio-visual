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
