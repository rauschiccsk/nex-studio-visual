# NEX Studio findings — Manažér nevidí, či sa stavba skvalitňuje alebo rozpadáva

**Zistené:** 12.09.2026, počas fázy Návrh na NEX Inbox v1.5.0
**Zdroj:** Úvaha Directora (Zoltán Rausch) počas behu, doložená priebehom tej istej stavby
**Pre koho:** backlog zlepšení NEX Studia Visual

---

## Čo sa stalo

Fáza Návrh na NEX Inbox v1.5.0 prešla **piatimi kolami** nezávislej previerky. Manažér počas nich
videl vždy len jeden údaj — koľko rozhodnutí práve čaká:

```
kolo 1   →   6 rozhodnutí
kolo 2   →   2 rozhodnutia
kolo 3   →   prechádza, „môže sa na Vizuál"
kolo 4   →   4 rozhodnutia        ← tu to vyzeralo, že sa to rozpadáva
kolo 5   →   prechádza
```

Po treťom kole bolo hotovo. Po štvrtom pribudli **štyri nové otázky**. Z obrazovky sa nedalo zistiť,
či sa niečo pokazilo, alebo naopak našlo.

**Director to pomenoval takto:** *„Ak vyriešime 5 otázok a následne vzniknú dva, to je v poriadku. Ale
keď vyriešime dva a vznikne 7, to je problém. Z hľadiska pracovníka to vyzerá, že neriešime, ale
rozbúrame už to, čo je."*

A dodal hranicu, ktorú tento nález **nesmie** prekročiť: *„V žiadnom prípade nechceme lacnú variantu…
kvalita, spoľahlivosť, nerobiť lacné riešenia — tie platia stále. Ja chcem, aby manažér videl a bol
informovaný, že práve skvalitňujeme a nerozbíjame to, čo už bolo."*

**Je to teda nález o informovanosti, nie o mechanike rozhodovania.** Nič v priebehu sa nemá zjednodušiť.

## Prečo je to vážne aj keď nič nie je pokazené

Dnes tento priebeh uniesli dvaja tvorcovia systému. **Tibor a Nazar ho uvidia bez toho kontextu** — a
rastúce číslo si prirodzene vysvetlia ako rozpad. Nasledovať bude buď strata dôvery v previerku, alebo
tlak previerku obísť. Oboje je horšie než to, čo dnes previerka stojí.

Pritom skutočný priebeh bol opačný, než ako vyzeral:

| kolo | blokujúce | menšie |
|---|---|---|
| 1 | **5** | 1 |
| 2 | **1** | 4 |
| 3 | **0** | 1 |
| 4 | **1** | 1 |
| 5 | **0** | 1 |

Blokujúcich **5 → 1 → 0 → 1 → 0**, spolu dvanásť uzavretých nálezov, z toho tri také, ktoré by prešli
všetkými skúškami a prejavili sa až u platiaceho zákazníka. **Tento obraz musel Dedo poskladať ručne
z databázy** — v kokpite nie je.

## Jadro: nové otázky nie sú jeden druh

Kľúč k celému nálezu. Pri tejto stavbe vznikali nové otázky z **troch rôznych príčin** a manažér ich
všetky prežíva rovnako, lebo vidí len počet:

| druh | čo sa stalo | ako to prežíva manažér | ako to má prežívať |
|---|---|---|---|
| **dôsledok** | rozhodnutie A rozbilo už hotové B | rozpad | oprávnene: niečo sme nedomysleli |
| **objav** | niečo bolo pokazené odjakživa a teraz sa to našlo | rozpad | **zisk** — nič sa nerozbilo, len prestalo byť neviditeľné |
| **odklad** | vedome odložené, teraz dozrelo | rozpad | plán, ktorý beží podľa dohody |

**Štvrté kolo — to, ktoré vyzeralo najhoršie — bolo čisto druhého druhu.** Nevyvolalo ho žiadne
rozhodnutie Manažéra. Vyvolalo ho to, že Auditor konečne overil tvrdenie, ktoré predtým trikrát len
prečítal: v Zadaní stálo, že dve príčiny varovania `NIB-112` si prepisujú zápis v zázname. V kóde to
neplatí. A na tom nepravdivom tvrdení stála **povinná stráž, ktorá by bola zelená od prvej sekundy** —
presne ten druh slepej stráže, kvôli ktorému bod 3 tej istej verzie vôbec existuje.

To tvrdenie napísal Dedo. Prešlo Zadaním, fázou Prípravy a tromi kolami previerky nedotknuté.

## Čo kokpit dnes vie a čo mu chýba

Overené v kóde 12.09.2026:

| údaj | stav |
|---|---|
| poradové číslo kola a strop (`round` / `round_max`) | **je** — `ConsultationBlock` |
| pôvod celej konzultácie (`source`: `auditor_upfront`, `verifikacia_fail`…) | **je** |
| pôvod **jednotlivého rozhodnutia** (dôsledok / objav / odklad) | **chýba** — `ConsultDecision` také pole nemá |
| blokujúce verzus menšie nálezy ako **údaj** | **chýba** — `findings: list[str]`, rozdiel je len v texte („(neblokujúce)“) |
| koľko rozhodnutí je už uzavretých | **dá sa spočítať** — zapísané odpovede s `consultation_decision` |
| vývoj počtu po kolách | **dá sa spočítať** — z verdiktov Auditora, ak budú štruktúrované |

**Základ sú teda dve malé zmeny v údajoch.** Bez nich sa nedá zobraziť nič z toho, čo nasleduje.

## Návrh

### 1. Dva nové údaje (základ pre všetko ostatné)

- `ConsultDecision.origin` — `dosledok` | `objav` | `odklad`. Pri `dosledok` aj to, ktorého rozhodnutia
  je to dôsledok.
- `finding.blocking` — pravda/nepravda ako **údaj**, nie ako slovo v texte.

Oboje vypĺňa ten, kto to vie: Agent pri stavbe kariet, Auditor pri verdikte.

### 2. Karta povie svoj pôvod

Namiesto holej otázky: *„Nález, ktorý tu bol vždy — nevznikol z tvojho rozhodnutia."* alebo
*„Vyplýva z tvojho rozhodnutia o pätičke."*

Nemení to počet otázok. Mení to, čo si manažér o tom počte myslí.

### 3. Cena voľby pri voľbe, nie po nej

Keď možnosť znovu otvára už uzavreté rozhodnutia, karta to má povedať **pri tej možnosti**:
*„táto voľba znovu otvára 2 uzavreté rozhodnutia."*

⚠️ **Ako údaj, nikdy ako odporúčanie.** Pri tejto stavbe boli dve miesta, kde bola drahšia cesta
správna (zmena skladania textu namiesto obchádzky; široký rozsah pri početných kontrolách). Keby sa
z tohto údaja stal tlak na lacnejšiu voľbu, vyrobí polovičné opravy — presne to, čo v1.5.0 opravuje.
**Toto je tvrdá hranica nálezu.**

### 4. Smer namiesto stavu

Nad kartami jeden riadok: *„Uzavretých 12 · otvorené 4 · blokujúcich 5 → 1 → 0 → 1 → 0"*.

Manažér nemá vidieť, koľko zostáva. Má vidieť, **kam to ide**.

### 5. Záver kola v ľudskej reči

Po každom kole veta, ktorá povie, čo sa získalo — nie čo pribudlo: *„Toto kolo zachytilo jednu vec,
ktorá by prešla všetkými skúškami a prejavila sa až u zákazníka."*

## Čo tento nález NEROBÍ

- **Nezjednodušuje priebeh.** Počet kôl, prísnosť previerky ani strop piatich kôl sa nemenia.
- **Neodporúča lacnejšie možnosti.** Ukazuje cenu, nevyberá za manažéra.
- **Nezakrýva zlé správy.** Keď sa stavba naozaj rozpadáva, musí to byť vidieť rovnako zreteľne.

## Poznámka k príčine

Štvrté kolo nevzniklo z rozhodovania, ale z **neovereného tvrdenia v Zadaní**. Žiadne poradie
rozhodnutí by mu nezabránilo. Bráni mu jediné pravidlo, a to pred Zadaním, nie v ňom:

> **Tvrdenie o správaní kódu, na ktorom stojí stráž, sa musí otvoriť a overiť** — nie preto, že o ňom
> pochybujeme, ale práve preto, že nepochybujeme. Vierohodné tvrdenie je nebezpečnejšie než zjavne
> pochybné, lebo ho nikto neotvorí.

Čím menej neoverených tvrdení vojde do Zadania, tým menej otázok neskôr vyrastie. Zobrazovanie
pokroku lieči prežívanie; toto lieči príčinu.
