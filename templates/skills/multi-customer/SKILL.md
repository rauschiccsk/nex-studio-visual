---
name: multi-customer
description: Jedna aplikácia pre viac zákazníkov (DEV-137) — spoločný kód, nastavenie zákazníka, zbierka skutočných dokladov. Použi pri oprave podľa dokladu zákazníka a keď chce iné správanie.
---

# Jedna aplikácia pre viac zákazníkov (DEV-137)

Director 16.10.2026: „…vidím nebezpečenstvo, že ak opravíme niečo pre jedného zákazníka pokazíme niečo u existujúceho.“
Každý zákazník má vlastnú inštaláciu, ale kód je spoločný — oprava pre jedného ide ku všetkým. Preto tri pravidlá:

1. **Doklad číta spoločný kód rovnako pre všetkých (P2).** Čo stojí na doklade dodávateľa (faktúra, dodací list,
   ISDOC), aplikácia rozpozná podľa znakov dokladu, nie podľa toho, ktorý zákazník ho poslal. Keď treba konkrétneho
   dodávateľa, je to jedna pomenovaná hodnota v spoločnom kóde (vzor: IČO REBIOP v NEX Inboxe) — nie nastavenie
   zákazníka a nie podmienka na meno zákazníka.
2. **Čím sa zákazník líši, je nastavenie jeho inštalácie (P3).** Keď dvaja zákazníci chcú ten istý prípad riešiť
   inak, vznikne voľba v nastaveniach s predvolenou hodnotou = doterajšie správanie, takže existujúci zákazník
   nemusí urobiť nič. Voľba vzniká až na požiadavku druhého zákazníka a po rozhodnutí Directora — v Príprave ju
   navrhni otázkou, sám ju nezakladaj. Nikdy nie proti zákonu ani proti platnosti výstupu (napr. Peppol XML).
3. **Skutočný doklad, na ktorom sa opravovalo, ostáva v projekte natrvalo (P4).** Do zbierky skutočných dokladov
   projektu pribudne: anonymizovaná kópia dokladu, nahraná odpoveď modelu AI a očakávaný výsledok pri nastaveniach
   daného zákazníka. Skúška v CI prejde celú zbierku — model sa nevolá (použije sa nahrávka), všetko ostatné beží
   naozaj. Doplnenie zbierky patrí do Špecifikácie každej opravy dokladu zákazníka. Projekt bez zbierky: navrhni ju
   v Príprave (tvar opisuje plán DEV-137, krok 2).

**Anonymizácia.** Originál od Manažéra je v `private/` a do gitu nikdy nejde. Do zbierky dávaš kópiu, v ktorej sú
osobné údaje ľudí (mená, adresy, telefóny, e-maily, účty fyzických osôb) nahradené vymyslenými; údaje firiem, na
ktorých oprava stojí (IČO, DIČ, sumy, čísla dokladov, čiarové kódy), ostávajú, inak by skúška nič neoverila.
