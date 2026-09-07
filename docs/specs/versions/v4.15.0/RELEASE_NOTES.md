# v4.15.0 — Verzia počká na výsledok kontrol, nepýta sa a neodíde

Poistku proti červeným kontrolám sme zaviedli v predošlých vydaniach. Fungovala —
len sa pýtala v okamihu, keď odpoveď ešte nemohla existovať.

## Čo sa dialo

Engine vytlačí hotovú prácu na GitHub a **o dvanásť milisekúnd** sa spýta, či pre ňu
existuje beh kontrol. GitHub ho v tej chvíli ešte nezaložil. Odpoveď „zatiaľ nič"
poistka podľa pravidla prepúšťa — neznalosť nemá zastavovať.

Zmerané 7. septembra na verzii 0.1.6 a 0.1.7 jedného projektu: obe prešli ako
overené, behy vznikli o sekundu neskôr a jeden z nich o desať minút **padol**.

## Čo sa mení

Engine teraz **počká na beh, ktorý sám vyvolal** — najprv kým sa objaví (do dvoch
minút), potom kým dobehne (do dvadsiatich). Rozhodne až podľa výsledku.

Kým čaká, napíše to do rozhovoru s číslom behu, aby stavba nevyzerala zaseknutá.

## Čo naďalej prejde

Skutočná neznalosť — projekt kontroly vôbec nemá, GitHub neodpovedá, beh sa
nedočkal konca ani po dvadsiatich minútach. Brána, ktorá zastavuje na neznalosti,
sa naučí obchádzať. **Ale zapíše sa, ktorá neznalosť to bola.** Doteraz sa nedalo
rozoznať „kontroly boli zelené" od „nikto sa nepýtal"; oboje vyzeralo rovnako.

Projekt bez nastavených kontrol sa nezdržiava vôbec — niet na čo čakať.
