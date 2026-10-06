"""Doplní spotrebu starších ťahov zo záznamov sedení (ICCINT-168) — :mod:`backend.services.usage_backfill`.

Bez ``--apply`` len vypíše výkaz po verziách a nič nezapíše. Spúšťa sa v kontajneri backendu kokpitu, ktorý vidí
záznamy sedení agentov aj Poradcu na tých istých cestách ako hostiteľ::

    docker exec nex-studio-visual-prod-backend-1 python -m backend.scripts.backfill_usage
    docker exec nex-studio-visual-prod-backend-1 python -m backend.scripts.backfill_usage --apply

Po zápise sa hneď doplnia cenníky — ťahy uzavreté Claude Code nesú jeho cenu a z nich sa cenník vyčíta.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.db.session import SessionLocal
from backend.services import build_sandbox, model_pricing, usage_backfill
from backend.services.poradca import sandbox


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Doplnenie spotreby starších ťahov zo záznamov sedení.")
    parser.add_argument("--apply", action="store_true", help="zapísať (bez neho len výkaz)")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        the_plan = usage_backfill.plan(
            db,
            claude_home=Path(build_sandbox._CLAUDE_HOME_DIR),
            poradca_sessions=sandbox.data_dir() / "sessions",
        )
        for line in usage_backfill.report(the_plan):
            print(line)
        print(f"behy v záznamoch, ktoré nepatria žiadnemu doplňovanému ťahu: {the_plan.unmatched_runs}")
        if not args.apply:
            print("NANEČISTO — nič sa nezapísalo. Zápis: --apply")
            return 0
        print(f"doplnených ťahov: {usage_backfill.apply(db, the_plan)}")
        print(f"nové cenníky: {model_pricing.refresh(db)}")
        for model, rows in sorted(model_pricing.price_rows(db).items()):
            for r in rows:
                print(
                    f"  {model} od {r.valid_from:%Y-%m-%d}: vstup {r.input_usd} $, výstup {r.output_usd} $, "
                    f"čítanie {r.cache_read_usd} $, zápis {r.cache_write_usd} $ za milión; "
                    f"kurz {r.eur_usd} $/€ ({r.rate_date}); overené na {r.observations} ťahoch"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
