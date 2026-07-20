# v4.0.15 — NEX Studio Visual

## Tá istá oprava sa už nezaradí do plánu viackrát

Keď záverečná Verifikácia opakovane našla tú istú chybu, do plánu úloh sa **pri každom pokuse pridala nová úloha „Oprava po Verifikácii"** — nakoniec ich tam bolo aj niekoľko za sebou a AI Agent by tú istú vec robil viackrát zbytočne.

Od tejto verzie:

- **Kým jedna „Oprava po Verifikácii" čaká alebo beží, nová sa už nepridá** — systém tú existujúcu znovu použije a len aktualizuje zadanie. Ďalšia vznikne až vtedy, keď je predošlá naozaj dokončená.
- Plán úloh zostáva prehľadný a agent nerobí tú istú opravu dvakrát.
