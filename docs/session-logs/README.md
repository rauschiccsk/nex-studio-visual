# Session Logs

Štruktúrovaný audit trail CC sessions v NEX Studio repe. Tracked v gite (na rozdiel od `.nex-session-state.md`, ktoré je živá pracovná pamäť a nie je commitované).

## Convention

- Path: `docs/session-logs/YYYY-MM-DD-NNN.md`
- `NNN` je 3-číselný sekvenčný index v rámci dňa (`001`, `002`, …)
- Vytvára sa na konci session pred „session end" commit-om
- Obsahuje **rozhodnutia** a **artefakty**, nie krok-po-kroku tool calls (na to je `.nex-session-state.md` + git history)

## Šablóna

```markdown
# Session YYYY-MM-DD-NNN

**Trigger:** [čo session naštartovalo — Zoltánov zámer / pokračovanie z predchádzajúcej]
**Outcome:** [jednou vetou globálny výsledok]

## Tracks
1. **[Názov tracku]** — [stručne čo, ako skončilo]
2. ...

## Key decisions
- [decision] — [rationale, kde je dokumentované]

## Artifacts
- Repo commits: `<hash>`, `<hash>`
- KB commits: `<hash>`
- KB files created/updated: ...
- Config changes (.nex-session-state.md, hooks, ...): ...

## Open items / next session entry point
- [čo zostalo / kde má ďalšia session začať]
```

## Vzťah k iným artefaktom

- `.nex-session-state.md` — *aktuálny* stav, prepisovaný; tu vidíš „kde sme teraz"
- Session logs (tieto) — *historický* záznam, append-only; tu vidíš „ako sme sa sem dostali"
- KB (`/home/icc/knowledge/`) — strategické rozhodnutia, decisions, lessons learned cross-project
- Git log — granulárna história zmien
