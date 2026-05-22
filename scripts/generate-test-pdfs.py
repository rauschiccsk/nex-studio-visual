#!/usr/bin/env python3
"""Generovať syntetické PDF z test-data-spec.md.

Per F-003 §4.4 + §6.1 spec — reportlab template-based PDF generation
zo Designer/Customer-agent kostry. Plus JSON metadata so očakávaným
extract output.

Anonymizácia (F-003 §6.1): vstupný spec musí obsahovať syntetické dáta
(vymyslené IČO, neexistujúci dodávatelia). Tento skript je pass-through —
anonymizácia sa vynucuje pri spec authoringu, nie tu.

Spustenie:
    python scripts/generate-test-pdfs.py <projekt>
    python scripts/generate-test-pdfs.py nex-inbox --version v0.2.0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _uat_lib  # noqa: E402
import frontmatter  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

PROJECTS_ROOT = Path("/opt/projects")


def parse_test_data_spec(spec_path: Path) -> list[dict]:
    """Read test-data-spec.md frontmatter and return list of scenarios."""
    raw = spec_path.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    scenarios = post.metadata.get("scenarios", [])
    if not isinstance(scenarios, list):
        raise ValueError(f"'scenarios' must be a list in {spec_path}")
    return scenarios


def generate_pdf(scenario: dict, output_dir: Path) -> Path:
    """Render scenario to a PDF file. Returns path to written PDF."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{scenario['id']}.pdf"

    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    # Header
    elements.append(Paragraph(f"<b>FAKTÚRA — {scenario['id']}</b>", styles["Title"]))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"Scenár: {scenario.get('scenario', '')}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    # Supplier
    supplier_data = [
        ["Dodávateľ", scenario.get("supplier", "")],
        ["IČO", scenario.get("supplier_ico", "")],
        ["IČ DPH", scenario.get("supplier_ic_dph", "")],
    ]
    supplier_table = Table(supplier_data, colWidths=[120, 300])
    supplier_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ]
        )
    )
    elements.append(supplier_table)
    elements.append(Spacer(1, 18))

    # Line items
    line_items = scenario.get("line_items", [])
    items_data = [["Popis", "Mn.", "Cena/ks", "Spolu"]]
    for item in line_items:
        qty = item.get("quantity", 0)
        price = item.get("unit_price", 0.0)
        items_data.append(
            [
                item.get("description", ""),
                str(qty),
                f"{price:.2f}",
                f"{qty * price:.2f}",
            ]
        )
    items_table = Table(items_data, colWidths=[200, 50, 80, 80])
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ]
        )
    )
    elements.append(items_table)
    elements.append(Spacer(1, 18))

    # Totals
    totals_data = [
        ["Bez DPH", f"{scenario.get('amount_net', 0.0):.2f}"],
        ["DPH", f"{scenario.get('amount_vat', 0.0):.2f}"],
        ["Spolu", f"{scenario.get('amount_total', 0.0):.2f}"],
    ]
    totals_table = Table(totals_data, colWidths=[120, 100])
    totals_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    elements.append(totals_table)

    doc.build(elements)
    return pdf_path


def generate_metadata(scenario: dict, output_dir: Path) -> Path:
    """Write expected-extract JSON metadata next to PDF."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{scenario['id']}.json"
    json_path.write_text(json.dumps(scenario, indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path


def run(project: str, *, version: str) -> int:
    _uat_lib.validate_slug(project)

    spec_path = PROJECTS_ROOT / project / "docs" / "uat" / version / "test-data" / "test-data-spec.md"
    if not spec_path.exists():
        _uat_lib.error_console.print(f"[red]ERROR:[/red] test-data-spec.md not found: {spec_path}")
        return 1

    output_dir = spec_path.parent / "synthetic"
    scenarios = parse_test_data_spec(spec_path)

    if not scenarios:
        _uat_lib.console.print("[yellow]No scenarios in spec — nothing to generate.[/yellow]")
        return 0

    _uat_lib.console.print(f"[cyan]Generating {len(scenarios)} synthetic PDFs[/cyan] → {output_dir}")
    for scenario in scenarios:
        if "id" not in scenario:
            _uat_lib.error_console.print(f"[red]ERROR:[/red] scenario missing 'id' field: {scenario}")
            return 1
        pdf_path = generate_pdf(scenario, output_dir)
        json_path = generate_metadata(scenario, output_dir)
        _uat_lib.console.print(f"  ✓ {pdf_path.name} + {json_path.name}")

    _uat_lib.console.print(f"\n[green]Done.[/green] Output in {output_dir}/")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generovať syntetické PDF z test-data-spec.md (F-003 §4.4).",
    )
    parser.add_argument(
        "project",
        help="Project slug (e.g. 'nex-inbox'). Spec read from "
        "/opt/projects/<project>/docs/uat/<version>/test-data/test-data-spec.md",
    )
    parser.add_argument(
        "--version",
        default="v0.2.0",
        help="UAT version directory (default: v0.2.0)",
    )
    args = parser.parse_args()

    try:
        return run(args.project, version=args.version)
    except ValueError as exc:
        _uat_lib.error_console.print(f"[red]ERROR:[/red] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
