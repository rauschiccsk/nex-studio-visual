# NEX Studio Visual — kokpit (repozitár)

Kokpit, v ktorom AI Agent a Audítor stavajú aplikácie zákazníkov a Manažér ich riadi. Tento repozitár je
**kokpit sám** — kokpit ho nestavia ako projekt. Vyvíja ho Andros Dedo na tiketoch evidencie DEVELOP (modul
NEX Studio Visual); rozhoduje Director.

Pravidlá pre agentov **stavieb** tu nie sú: kokpit ich skladá zo šablón v `templates/` a zapisuje do každého
projektu (`backend/services/create_project_postscaffold.py`). Tento súbor je len pre prácu na kokpite.

## Kde čo je

- `backend/` — FastAPI; priebeh stavby `services/orchestrator.py`, nasadenie `services/deploy.py` a
  `services/uat_provisioner.py`, nastavenia a stropy `config/settings.py`.
- `migrations/versions/NNN_*.py` — Alembic; backend ich pri štarte spúšťa sám.
- `frontend/` — React + Vite; typy API `src/services/api/pipeline.generated.ts` sa generujú.
- `templates/` — charty agentov stavieb (`agent-shared-base.md` + `ai-agent-charter.md` / `auditor-charter.md`),
  `project-claude-md.md` (koreňový CLAUDE.md projektu), `poradca-charter.md` a zručnosti agentov stavieb
  `skills/<meno>/SKILL.md` (DEV-46) — postup, ktorý agent potrebuje len niekedy, patrí do zručnosti a charta ho
  menuje jednou vetou. Text, ktorý agent dostane pri každej práci (charta, koreňový CLAUDE.md, opisy zručností),
  má strop **37 000 znakov** (Director 09.10.2026, DEV-45) — stráž
  `tests/test_agent_instructions_stay_under_the_ceiling.py`; pri prekročení sa skracuje text, nie strop.
  Charty aj zručnosti zapisuje do projektu `create_project_postscaffold.py` pri založení a obnovuje ich pred každým
  novým sedením agenta, aj v prevzatých projektoch (`claude_agent._refresh_rules`, DEV-47). Zmena teda platí od
  najbližšieho nového sedenia po nasadení kokpitu; pokračujúce sedenie si drží chartu, s ktorou začalo.
- `docs/specs/versions/vX.Y.Z/RELEASE_NOTES.md` — poznámka k vydaniu po slovensky pre toho, kto kokpit používa.
- `scripts/deploy-prod.sh` — nasadenie na PROD.

## Skúšky a brány — tak, ako ich púšťa CI (`.github/workflows/ci.yml`)

- Backend z koreňa (testpaths `tests` aj `backend/tests`), **vždy s `DOCKER_HOST=unix:///nonexistent`** — inak
  skúšky siahajú na živý Docker servera (03.10.2026 zmazali databázu bežiacej stavby):
  `DOCKER_HOST=unix:///nonexistent poetry run pytest --tb=short -q --cov=backend --cov-report=term --cov-fail-under=88`
  a `poetry run ruff check .`, `poetry run ruff format --check .`.
- Frontend vo `frontend/`: `npm run type-check`, `npm run lint`, `npm test`, `npm run build`. Po zmene trasy
  alebo schémy backendu `npm run codegen` a commit vygenerovaných typov — inak CI zlyhá na rozdiele.
- Nová skúška: najprv ju vidieť zlyhať, potom zmutovať v oboch smeroch.

## Pravidlá

- Zdrojový kód po anglicky (mená, komentáre); texty pre človeka v aplikácii a dokumentácia po slovensky.
- Port, adresa, strop, limit patria do `backend/config/settings.py`, žiadna hodnota v dvoch súboroch.
- Tajomstvá sa nikdy nevypisujú; `.env` je mimo gitu.
- Nasadenie na PROD len po zelenom CI, so súhlasom Directora a keď nepracuje žiadny agent (reštart backendu by
  prerušil stavby). Overenie po nasadení: `/health` (verzia), `/api/v1/release-notes`, balík obrazovky, kontajnery.
