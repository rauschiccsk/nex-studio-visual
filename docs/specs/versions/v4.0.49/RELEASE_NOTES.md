# v4.0.49 — NEX Studio Visual

## Keď sa samotné overenie zasekne, dá sa čisto spustiť znova

Vo Verifikácii appku posúdi nezávislý Audítor. Ak sa **jeho vlastný beh zasekol** (napr. časový limit) — a appka pritom prešla skúškou po spustení — jediná ponúkaná možnosť viedla na opravára (AI Agent), lenže **nebolo čo opravovať**. To zbytočne spúšťalo opravnú slučku a mohlo sa zacykliť.

Opravené:

- Keď zlyhá **samotný overovací beh** (nie appka), cockpit teraz ponúka jasné tlačidlo **„Znova spustiť overenie"** — priamo zopakuje koncové overenie (spustí aplikáciu + nezávislý Audítor ju posúdi), **bez** zbytočného opravára. Ak prejde, verzia je pripravená na schválenie (Hotovo).
- Hlásenie je poctivé pre daný prípad: pri zaseknutom overení sa už nepíše „chyba bola mimo projektu", ale „overenie sa zaseklo — spusti ho znova".

Pre neexperta to znamená jednoznačnú akciu presne vtedy, keď treba len znova spustiť kontrolu.
