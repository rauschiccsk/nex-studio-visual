# v4.0.8 — NEX Studio Visual

## Spoľahlivé záverečné overenie + zrozumiteľné nálezy z previerky

Pri kroku **Verifikácia** NEX Studio spustí hotovú appku a otestuje, či naozaj robí to, čo sľubuje zadanie. Ukázalo sa, že keď appka neponúka žiadny „domáci" port navonok (bežná a správna konfigurácia), overovanie sa spájalo na nesprávne miesto — a tak fakticky neoverilo nič, no napriek tomu vypísalo blokujúci nález. Manažéra to zbytočne zastavilo, hoci samotná appka bola v poriadku.

A nálezy z nezávislej previerky (Auditora) boli písané technickým žargónom — cesty k súborom, názvy portov a premenných, počty testov. Manažér ani junior operátor z toho ťažko prečítal, čo vlastne treba rozhodnúť.

Od tejto verzie:

- **Záverečné overenie sa vždy spojí so správnou appkou** — aj keď appka nepublikuje žiadny port navonok. Koniec falošných blokujúcich nálezov, keď je produkt v skutočnosti zelený.
- **Nálezy z previerky sú po slovensky, ľudsky** — Auditor povie, ČO nefunguje a čo treba rozhodnúť z pohľadu používateľa, v pár vetách. Technické detaily idú do návrhu opravy, nie do textu pre Manažéra.
