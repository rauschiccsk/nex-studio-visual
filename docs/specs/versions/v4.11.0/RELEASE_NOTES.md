# v4.11.0 — Po spustení rýchlej opravy vidíš, že beží

Keď si spustil rýchlu opravu z pripraveného návrhu, lišta zmizla a kokpit zostal
stáť na verzii, ktorú si mal otvorenú. Nová verzia pritom vznikla a agent na nej
už pracoval — len si ho nemal kde vidieť.

Vyzeralo to ako zlyhanie. Nebolo.

## Prečo sa to dialo

Rýchla oprava je **jediná akcia v kokpite, ktorá zakladá novú verziu** — všetky
ostatné pokračujú v tej, ktorú máš otvorenú. Je to teda jediné miesto, kde
„ostávam tam, kde som" neplatí.

Pôvodné tlačidlo *Rýchla oprava* na projekte to vedelo a na novú stavbu ťa preplo.
Lišta s návrhom, ktorá pribudla neskôr ako druhá cesta k tomu istému, ten posledný
krok nerobila.

## Čo sa mení

Po spustení rýchlej opravy ťa kokpit **prepne na stavbu, ktorá práve vznikla** —
rovnako, či ju spustíš tlačidlom alebo z pripraveného návrhu.

Ten krok je teraz na jednom mieste a používajú ho obe cesty, takže ho tretia
cesta nemôže znova vynechať.

## Čo sa nemení

Pri všetkých ostatných slovesách — vrátiť agentovi, odpovedať, spýtať sa,
odpovedať na kartu rozhodnutia — kokpit **zostane tam, kde si**. Tie pokračujú
v tej istej stavbe a prepínať by bolo zlé. Aj na to je test.
