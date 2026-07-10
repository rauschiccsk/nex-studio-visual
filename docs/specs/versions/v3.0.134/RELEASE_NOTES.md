## v3.0.134 — Spoľahlivejšie overenie a čistejší cockpit

### Overenie appky už spoľahlivo naštartuje appku
Pri záverečnom overení sa appka niekedy ani nespustila kvôli chýbajúcemu nastaveniu hesla databázy. Teraz sa overovacie prostredie vždy naplní kompletnými hodnotami zo šablóny appky, takže sa appka riadne rozbehne a dá sa overiť.

### Keď sa appka nespustí, povieme ti pravdu
Ak by sa appka pri overení predsa len nespustila, dostaneš **jasný dôvod** — „Appka sa nespustila: …" — namiesto mätúceho hlásenia „verdikt sa nepodarilo spracovať". Vieš hneď, čo sa deje.

### Tlačidlo po dokončenej stavbe dáva zmysel
Keď je stavba hotová, tlačidlo teraz píše **„Prejsť na overenie"** (a posunie projekt na Verifikáciu), nie zavádzajúce „Schváliť plán" — plán je už dávno schválený.

### Čistejšie názvy v pláne úloh
AI už nevpisuje „EPIC 1", „EPIC 2" … do názvov epík. Číslovanie robí systém, názvy zostávajú čisté a čitateľné.
