# v4.53.0

**Kontroly projektu (CI) bežia v oddelenom priestore, nie na ostrom serveri.** Kokpit doteraz každému novému projektu
spustil vykonávač kontrol, ktorý pracoval priamo s Dockerom servera — tým istým, v ktorom bežia inštalácie zákazníkov.
Kontrola tak mohla zastaviť či zmazať cudziu databázu a čo po zrušenej kontrole ostalo bežať, bežalo ďalej (v októbri
štyri dni). Teraz dostane každý projekt vlastný oddelený Docker: kontroly ostrý server nevidia a na začiatku aj na konci
každej kontroly sa ich priestor vyprázdni. Vykonávače existujúcich projektov sa prevedú na nový tvar; prebiehajúcej
kontroly sa prevod nedotkne.
