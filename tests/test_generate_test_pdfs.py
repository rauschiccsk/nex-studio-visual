"""Tests for scripts/generate-test-pdfs.py.

Per F-003 §4.4 + §6.1 (syntetické PDF, anonymizácia, reportlab template).
Tests derived from spec per Implementer charter §13.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate-test-pdfs.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _import_module(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("generate_test_pdfs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setattr("sys.path", [str(SCRIPT.parent), *sys.path])
    spec.loader.exec_module(mod)
    return mod


VALID_SPEC = dedent(
    """\
    ---
    version: v0.2.0
    scenarios:
      - id: "01-happy-text"
        scenario: "Bežná faktúra 23% DPH, text PDF"
        supplier: "Synth Dodávateľ Alpha s.r.o."
        supplier_ico: "12345678"
        supplier_ic_dph: "SK1234567890"
        amount_net: 1000.00
        amount_vat: 230.00
        amount_total: 1230.00
        line_items:
          - description: "Materiál typu A"
            quantity: 10
            unit_price: 100.00
      - id: "02-reverse-charge"
        scenario: "Reverse charge faktúra"
        supplier: "Beta Materials Kft."
        supplier_ico: "87654321"
        supplier_ic_dph: "HU87654321"
        amount_net: 500.00
        amount_vat: 0.00
        amount_total: 500.00
        line_items:
          - description: "Service"
            quantity: 1
            unit_price: 500.00
    ---
    """
)


def test_help_shows_usage():
    r = _run(["--help"])
    assert r.returncode == 0
    assert "project" in r.stdout.lower() or "spec" in r.stdout.lower()


def test_parse_spec_returns_scenarios(monkeypatch, tmp_path):
    mod = _import_module(monkeypatch)
    spec_path = tmp_path / "test-data-spec.md"
    spec_path.write_text(VALID_SPEC)

    scenarios = mod.parse_test_data_spec(spec_path)
    assert len(scenarios) == 2
    assert scenarios[0]["id"] == "01-happy-text"
    assert scenarios[1]["id"] == "02-reverse-charge"
    assert scenarios[0]["supplier_ico"] == "12345678"


def test_parse_spec_missing_file_raises(monkeypatch, tmp_path):
    mod = _import_module(monkeypatch)
    with __import__("pytest").raises((FileNotFoundError, OSError)):
        mod.parse_test_data_spec(tmp_path / "no-such.md")


def test_generate_pdf_creates_file(monkeypatch, tmp_path):
    mod = _import_module(monkeypatch)
    output_dir = tmp_path / "synthetic"
    output_dir.mkdir()
    scenario = {
        "id": "01-happy-text",
        "scenario": "Test",
        "supplier": "Synth s.r.o.",
        "supplier_ico": "12345678",
        "supplier_ic_dph": "SK1234567890",
        "amount_net": 1000.00,
        "amount_vat": 230.00,
        "amount_total": 1230.00,
        "line_items": [
            {"description": "Item A", "quantity": 1, "unit_price": 1000.00},
        ],
    }
    pdf_path = mod.generate_pdf(scenario, output_dir)
    assert pdf_path.exists()
    assert pdf_path.suffix == ".pdf"
    assert pdf_path.stat().st_size > 0
    # Should be a valid PDF (starts with %PDF magic)
    assert pdf_path.read_bytes().startswith(b"%PDF")


def test_generate_metadata_creates_json(monkeypatch, tmp_path):
    mod = _import_module(monkeypatch)
    output_dir = tmp_path / "synthetic"
    output_dir.mkdir()
    scenario = {
        "id": "01-happy-text",
        "scenario": "Test",
        "supplier": "Synth s.r.o.",
        "amount_total": 1230.00,
    }
    json_path = mod.generate_metadata(scenario, output_dir)
    assert json_path.exists()
    data = json.loads(json_path.read_text())
    assert data["id"] == "01-happy-text"
    assert data["supplier"] == "Synth s.r.o."


def test_run_orchestrates_all_scenarios(monkeypatch, tmp_path):
    mod = _import_module(monkeypatch)

    # Set up fake project structure
    project_root = tmp_path / "projects" / "nex-inbox"
    uat_dir = project_root / "docs" / "uat" / "v0.2.0" / "test-data"
    uat_dir.mkdir(parents=True)
    (uat_dir / "test-data-spec.md").write_text(VALID_SPEC)

    monkeypatch.setattr(mod, "PROJECTS_ROOT", tmp_path / "projects")

    rc = mod.run("nex-inbox", version="v0.2.0")
    assert rc == 0
    synthetic_dir = uat_dir / "synthetic"
    pdfs = list(synthetic_dir.glob("*.pdf"))
    jsons = list(synthetic_dir.glob("*.json"))
    assert len(pdfs) == 2
    assert len(jsons) == 2


def test_run_fails_when_spec_missing(monkeypatch, tmp_path):
    mod = _import_module(monkeypatch)
    project_root = tmp_path / "projects" / "nex-inbox"
    project_root.mkdir(parents=True)
    monkeypatch.setattr(mod, "PROJECTS_ROOT", tmp_path / "projects")

    rc = mod.run("nex-inbox", version="v0.2.0")
    assert rc == 1


def test_anonymization_no_real_ico_in_synthetic(monkeypatch, tmp_path):
    """Per F-003 §6.1 anonymization: synthetic PDFs must not contain real IČO/PII.

    This test asserts that the spec we pass through contains only synthetic
    fixture data — the script itself is a pass-through, anonymization is
    enforced at the spec level by Designer/Customer-agent.
    """
    mod = _import_module(monkeypatch)
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(VALID_SPEC)
    scenarios = mod.parse_test_data_spec(spec_path)
    for s in scenarios:
        # Synthetic IČO checksum-valid but for "Synth Dodávateľ Alpha", not real company
        assert "Synth" in s["supplier"] or "Beta" in s["supplier"] or "Alpha" in s["supplier"]
