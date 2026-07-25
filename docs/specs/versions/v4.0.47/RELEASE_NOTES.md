# v4.0.47 — NEX Studio Visual

## Verifikácia sa prestane zacykľovať — poctivé hlásenie + oprava sa overuje tak ako naozaj beží

Keď Verifikácia našla chybu a AI Agent ju „opravil", niekedy sa **tá istá chyba vrátila dokola**. Príčina bola dvojaká — a obe sme odstránili:

- **Poctivé hlásenie chyby.** Ak appka **nabehla**, ale zlyhala kontrola stránky **Aktualizácie**, systém to predtým hlásil ako „appka sa nespustila". To zavádzalo — hľadalo sa zlé miesto. Teraz sa hlásenie líši: keď appka bežala a padla len konkrétna kontrola, píše sa to tak („aplikácia sa spustila, ale kontrola Aktualizácie zlyhala").

- **Oprava sa overuje v reálnom behu, nie len testami.** AI Agent má teraz v pravidlách jasne dané: oprava chyby z Verifikácie je „hotová" AŽ keď ju **overí spustením appky v kontajneri** (tak, ako ju spúšťa engine), nie len testami na svojom počítači. Rozdiel medzi „u mňa to prešlo" a „v nasadenej appke to padá" bol presne to, čo spôsobovalo zacyklenie.

- **Každá nová aplikácia si Aktualizácie kontroluje sama.** Do vzoru automatickej skúšky každej generovanej appky pribudla kontrola, že stránka Aktualizácie vracia **čisté číslo verzie** (nie prázdno a nie text nadpisu). Túto triedu chyby tým chytíme hneď, u každej budúcej appky.

Pre teba to znamená, že tlačidlo „Nechaj to opraviť" oveľa spoľahlivejšie problém vyrieši napevno.
