# v4.0.3 — NEX Studio Visual

## Spoľahlivejšia nezávislá previerka pred stavbou

Pred programovaním beží nezávislá previerka návrhu. Doteraz mohla občas posúdiť **ešte nedokončené** dokumenty (kým ich AI dopisovala) a nahlásiť „chyby", ktoré už boli vyriešené — a keď sa previerka s AI nezhodli, na obrazovke ostalo len „posúď klasicky" bez toho, čo vlastne rozhodnúť.

Od tejto verzie:

- **Previerka hodnotí zmrazený stav.** Návrh sa pred ňou uloží (commit), takže sa posudzuje hotový dokument, nie rozrobený. Zároveň sa tým rozpracovaná práca priebežne zálohuje.
- **Keď sa previerka a AI nezhodnú, uvidíš OBE strany** — čo previerka vytkla aj čo na to AI odpovedala — a rozhodneš so všetkým kontextom, nie naslepo.
