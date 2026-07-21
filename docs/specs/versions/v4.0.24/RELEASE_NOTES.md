# v4.0.24 — NEX Studio Visual

## Cockpit ťa pri novej verzii upozorní na novší nex-shared (opt-in povýšenie)

Aplikácie zdieľajú dizajnový kit **nex-shared** a každá je pripnutá na konkrétnu verziu. Doteraz sa appka na novšiu verziu kitu dostala len ručne. Od tejto verzie ťa na to cockpit **sám upozorní** — presne ako opt-in povýšenie balíka vo `venv`.

Pri založení **novej verzie** aplikácie:

- **Ak je appka pozadu** za najnovším nex-shared, objaví sa ponuka: porovnanie verzií (`teraz → najnovšia` + koľko verzií pozadu), **„Čo prinesie"** (z changelogu nex-shared, so značkami `[vzhľad]`/`[API]`/`[nové]`/`[oprava]`) a tlačidlá **Povýšiť** / **Ostať**.
- **Po „Povýšiť"** sa pin appky prepíše na zvolenú verziu a commitne — nová verzia (jej náhľad Vizuál aj build) už beží na novom nex-shared. Nový vzhľad tak uvidíš hneď v náhľade.
- **Po „Ostať"** sa nič nezmení; pri ďalšej novej verzii sa cockpit spýta znova.
- **Ak je appka na najnovšej**, ponuka sa vôbec neukáže (žiadny šum).

Rozhoduje sa **per verzia, per aplikácia, opt-in** — nič sa nepovyšuje na pozadí a iné aplikácie sú izolované (majú vlastný pin).
