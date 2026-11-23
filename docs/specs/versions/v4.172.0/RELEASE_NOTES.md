# v4.172.0

**Prevod na šablónu aplikácie zdvihne balík, ktorý šablóna potrebuje vyšší.** Projekt postavený pred šablónou mal
niektorý balík frontendu v staršej verzii, než šablóna (nex-app-kit) vie použiť — napríklad smerovanie
`react-router-dom` vo verzii 6, kým šablóna potrebuje 7. Prevod preto vždy zlyhal. Teraz taký balík zdvihne na verziu
šablóny a po prevode povie, ktoré balíky zdvihol; kód im prispôsobí Staviteľ vo verzii, ktorú práve zakladáš. Vyššiu
verziu projektu prevod nikdy neznižuje.

**Zlyhaný prevod povie skutočný dôvod.** Keď prevod predsa nepôjde, správa povie, ktorý balík a akú verziu šablóna
žiada a čo má projekt — nie len „npm install“ — a kokpit ho ohlási ako odmietnutie, nie ako chybu servera.
