# v4.0.43 — NEX Studio Visual

## Dokumenty, Zásobník a členovia tímu vidí aj vlastník

Po tom, ako AI Agent vytvoril špecifikáciu, karta **Dokumenty** ostala prázdna — hoci dokument existoval. Tri časti systému (Dokumenty, Zásobník, členovia projektu) totiž ostali prístupné len správcovi; pri rozšírení oprávnení sa vynechali. Opravené:

- **Dokumenty** — používateľ teraz vidí a otvára dokumenty **svojich** projektov (špecifikácia, zadanie, atď.); správca vidí všetko.
- **Zásobník** aj **členovia projektu** — rovnako obmedzené na vlastné projekty.

Bezpečnostne: každý vidí len dokumenty a zásobník projektov, ktoré sám vytvoril; úprava dokumentu je pre vlastníka alebo správcu.
