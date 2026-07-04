# NEX Studio — nový návrh od základu

> **Živý návrh.** Nahrádza starý v2 návrh, ktorý bol postavený na chybnom predpoklade „AI nahrádza manažéra".
> Vzniká v Dedo↔Zoltán dialógu 2026-07-03. Základ v pamäti: `project_nex_studio_agent_manager_model`.
> Postup: navrhujeme po kúskoch → na každom sa zhodneme → až potom implementácia. Do existujúceho projektu
> sa NIČ nedolepuje; BE aj FE sa môžu zásadne zmeniť.

---

## 1. Tri zásady (základ, z ktorého všetko vychádza)

1. **Vzťah AI Agent ↔ manažér projektu = Dedo ↔ Zoltán.** Partner, nie náhrada. Manažér je VŽDY pri kormidle.
2. **Všetko v NEX Studio slúži jednému:** aby bola vývojová práca pre manažéra čo najprehľadnejšia.
3. **Komunikácia s manažérom je po ľudsky** — presne ako sme si to nastavili (celé vety, príbeh, žiadne kódy/žargón).

---

## 2. Severka (náš meter na každé rozhodnutie)

**Jednou vetou:** NEX Studio je **Riadiace centrum**, kde manažér a jeho AI partner staviate softvér spolu — presne ako to robia Zoltán a Dedo teraz — len manažér vidí všetko jasne a je to príjemné miesto na prácu.

Z pohľadu manažéra:
- Príde so zámerom; partner sa s ním **rozpráva** (pýta správne otázky, pochopí problém, navrhne prístup), poladia, dohodnú sa.
- Partner **robí robotu** (zadanie, plán úloh, programovanie) a manažér to **vidí** — kde je v postupe, ako sa plní plán, čo práve robí. Nič skryté.
- Pri rozhodnutí / nejasnosti partner **vysvetlí po ľudsky a opýta sa** — jedno naraz, s odporúčaním; manažér rozhodne.
- Partner je **čestný**: povie čo je hotové a čo vratké, sám vytiahne riziko, sám si prekontroluje robotu.
- Manažér je **stále v obraze a pri kormidle** — nikdy nie divák ani odklepávač.
- Je to **kultúrne miesto**: vidí viac než v holom termináli (stav, postup, plán, metriky), rozhovor znie ako s človekom.

**Čím to NIE JE:** stroj čo stavia sám kým manažér pozerá; pevnosť čo rozhoduje zaňho alebo skrýva; miesto kde je manažér nahradený či zredukovaný na klikanie.

> Meter: každá časť musí slúžiť tejto severke. Ak niečo mení PRINCÍP namiesto toho, aby robilo prácu jasnejšou, patrí preč.

---

## 3. Priebeh stavby projektu (z pohľadu manažéra)

### Potvrdené (Manažér projektu 2026-07-03)
1. **Založenie projektu** — manažér cez funkciu „Create new project". Tu zaškrtne aj príznak **„Vývoj na
   zákazku"** (ak ide o zákaznícky projekt) — to jediné povoľuje odchýliť sa od jednotného dizajnu (viď §4).
2. **Nová verzia + Zadanie (nepovinné).** Manažér založí novú verziu. Ak zákazník poslal požiadavky svojím
   spôsobom (často chaoticky, neprofesionálne), manažér ich vloží ako **Zadanie** — je to len SUROVÝ vstup /
   informácia. Ak zákazník nič neposlal, Zadanie nie je.
3. **Špecifikácia cez konzultáciu.** Výsledný dokument je VŽDY **Špecifikácia** — systematická dokumentácia, kde
   je VŠETKO potrebné na vyhotovenie projektu. Vzniká **interaktívnym rozhovorom** manažéra a partnera:
   - ak Zadanie JE → partner ho použije ako vstup a spolu ho pretavíte do Špecifikácie;
   - ak Zadanie NIE JE → Špecifikáciu robíte od nuly, manažér postupne vysvetľuje, čo chce, tou istou ľudskou
     formou.
   Hotová Špecifikácia je **jeden trvalý `.md` dokument**: uloží sa do **knowledge dokumentácie** (sekcia
   projektu — pre NEX Studio `projects-nex-studio`) A ZÁROVEŇ je **kedykoľvek čitateľná priamo v NEX Studio** —
   položka **„Špecifikácia"** (napr. v ľavom sidebare), jedno kliknutie. Jeden dokument, dostupný oboma cestami.

### Pokračovanie (Manažér projektu 2026-07-03)
4. **Prejdenie a schválenie Špecifikácie.** Prejdete ju spolu v rozhovore — schváliš alebo poladíš. Je to
   základ; dotiahne sa načisto ešte pred stavbou.
5. **Plán práce = TVOJA mapa** (nie technický zoznam programátora). Agent rozloží schválené zadanie na kroky a
   napíše ich **jasnou ľudskou slovenčinou**; každá úloha **podrobne vysvetlená**, aby mal manažér čo najmenej
   doplnkových otázok. Tvorba plánu je **rozhovor** — nejasnosti hneď prekonzultuješ a **plán sa opraví na
   mieste**, kým sa vôbec začne stavať. Programátorov technický detail (súbor/funkcia) sedí POD mapou, na
   rozkliknutie (nič nie je skryté) — nie je to tvoj východiskový pohľad.
6. **Programovanie.** Agent programuje úlohu po úlohe; ty to vidíš naživo (plán sa plní, vidíš čo práve robí).
   Pri rozhodnutí/nejasnosti agent vysvetlí a opýta sa — jedno naraz, s odporúčaním — ty rozhodneš.
7. **Čestná kontrola.** Agent si sám prekontroluje robotu oproti zadaniu (robí to naozaj, čo sľúbilo?) a povie
   po ľudsky, čo je pevné a čo vratké. Prejdete to spolu.
8. **Hotovo.** Keď si spokojný, verzia je hotová. Nasadenie naživo (deploy) je samostatný vedomý krok, o ktorom
   rozhodneš ty.

Cez celý priebeh: rozhovor je chrbtica, prostredie ti ukazuje kde čo je (postup, plán, stav, metriky), ty si
stále pri kormidle.

---

## 4. Firemné zásady — NAD všetkými projektami („NEX firemný základ")

NIE sú súčasťou jedného projektu — žijú na JEDNOM centrálnom, verzovanom mieste a NEX Studio ich uplatní
**automaticky na KAŽDÝ projekt** (agent ich má vždy záväzne „v krvi"; manažér ich nezadáva znova, môže ich
vidieť; meníme ich na jednom mieste → platí pre všetky ďalšie projekty). Firemný zámer: **„Nechceme byť ako
konkurencia, chceme byť lepší" → značka NEX = maximálna kvalita.**

**Vždy:**
- Maximálna KVALITA a SPOĽAHLIVOSŤ zvoleného riešenia.
- Každý detail poriadne premyslený a navrhnutý.
- Všetko dotiahnuté do úspešného 100 % stavu.
- Žiadne opakovanie kódu — čo sa používa viackrát, ide do `nex-shared`.
- Jednotný dizajn pre všetky projekty ako DEFAULT; meniteľný LEN pri príznaku „Vývoj na zákazku" (krok 1).

**Nikdy:**
- Dočasné riešenia, ktoré bude časom treba prerábať.
- Horšie riešenie len preto, že je veľa práce / lacnejšie / rýchlejšie.
- Lacná náhrada zložitejšieho riešenia so sľubom „potom poriadne" — buď sa celá úloha odloží do ĎALŠEJ verzie,
  alebo sa spraví hneď poriadne.

> **Potvrdené (Manažér projektu 2026-07-03):** firemné zásady žijú na JEDNOM centrálnom mieste, uplatnené automaticky na
> každý projekt. **Kde a ako (rozhodnuté 2026-07-03):** jeden verzovaný dokument **„NEX firemný základ"
> v knowledge** (jediný zdroj pravdy, ICC-wide; ICC štandardy sú kostra). NEX Studio ho automaticky pridá
> partnerovi medzi záväzné pravidlá pri každom spustení a manažérovi ho ukáže na jedno kliknutie; zmena na
> tom jednom mieste → platí pre všetky ďalšie projekty. (Ostatné rozhodnutia z konzultácie o rozsahu: `REDESIGN-SCOPE.md`.)
> **Názov prostredia:** **„Riadiace centrum"** (nahrádza „kokpit").

---

## 5. Riadiace centrum — ako vyzerá (Manažér projektu 2026-07-03)

- **V strede — rozhovor s partnerom.** Srdce. Pýtaš sa, partner vysvetľuje, dohadujete sa, oslovuje ťa keď
  treba rozhodnúť. Znie ako rozhovor s človekom.
- **Vedľa — tvoja mapa práce** (plán úloh po ľudsky): čo staviame, čo je hotové, čo ostáva; rozklikneš na detail.
- **Navrchu — kde sme v priebehu:** zadanie → plán → programovanie → kontrola → hotovo. Jeden pohľad = vieš,
  v ktorej časti stavby si.
- **Vždy poruke — čestný stav:** čo sa práve deje, čo je pevné a čo vratké — bez hľadania.
- **Hlbšie na dosah (nie v ceste):** technický detail, metriky, história — na jedno kliknutie; inak nezavadzajú.
- **Kľúčové dokumenty na jedno kliknutie** (Manažér projektu): napr. **„Špecifikácia"** v ľavom sidebare — trvalý
  dokument projektu, čitateľný v NEX Studio kedykoľvek (tá istá `.md` čo je aj v knowledge).

Ťažisko: **rozhovor v strede, mapa a stav vedľa, všetko ostatné na dosah, nie na očiach.**

---

## 6. Rozhovor — ako funguje (srdce princípu) (Manažér projektu 2026-07-03)

- **Obojsmerný, kedykoľvek.** Manažér píše partnerovi kedy chce (otázka, oprava, nový nápad); partner
  priebežne hovorí, čo robí a prečo. Nikdy tichý stroj.
- **Hovorí ako človek** — po ľudsky, bez žargónu a vysypaných kódov.
- **Proaktívny a čestný** — sám vytiahne riziko / vratkú časť; nečaká na otázku, nič neschováva.
- **Rozhodnutia priamo v rozhovore** — keď treba voľbu manažéra, partner vysvetlí po ľudsky, dá možnosti +
  odporúčanie, JEDNO NARAZ; manažér rozhodne rovno tam. Žiadna oddelená obrazovka s verdiktmi a tlačidlami.
- **Voľby predkladá striedmo a čestne** (Manažér projektu): NEponúka zbytočné alternatívy, ktoré nie sú v súlade s
  firemnými zásadami (jedno najlepšie riešenie — §4). Len keď JE reálna voľba a **obe možnosti spĺňajú zásady**:
  **vysvetlí každú → porovná výhody/nevýhody → dá svoje odporúčanie → zdôvodní prečo.**
- **Rytmus (čestne):** písať sa dá vždy; správa poslaná počas kroku doletí, len čo krok dokončí (ako u Deda) —
  prirodzený rytmus, a je vidno, že správa čaká (krok sa nepreruší v polovici).

---

## 7. AI partner — JEDEN (Manažér projektu 2026-07-03)

Jeden AI partner („Dedo"), s ktorým manažér hovorí 1:1 — **robí robotu AJ si ju sám poctivo prekontroluje.**
Žiadny oddelený kontrolór/rola, s ktorou by sa manažér bavil (princíp 1:1 sa nedrobí). „Nezávislá kontrola"
(krok 7) = partner sa pozrie na vlastnú robotu **naozajstným odstupom** (čerstvý, nezaujatý pohľad, druhými
očami bez „materského puta" k tomu, čo napísal) — tak ako Dedo. Vstavaný rigor, nie samostatná postava.

---

## 8. Istota kvality — „hotovo" = ukázaná skutočnosť (Manažér projektu 2026-07-03)

- Partner nepovie len „hotovo, skontroloval som" — **ukáže dôkaz**: spustí to, tu je čo sa reálne stalo, toto
  funguje, toto je vratké. Manažér vidí REALITU, nie tvrdenie.
- „Hotovo" = **preukázaná skutočnosť, ktorú manažér videl a schválil** — nie odklepnutý verdikt ani sľub.
  (Stará chyba: „hotovo" = „kontrolór povedal PASS", nie „naozaj to funguje".)
- Nedotiahnuté sa **neschová ani nevydá za hotové** — buď sa dorobí poriadne, alebo celá úloha ide do ďalšej
  verzie (firemné zásady).
- **Aj kontrola je ROZHOVOR:** ak sa manažérovi niečo nezdá, opýta sa; ak chce výsledok kontroly podrobnejšie
  vysvetliť, povie — partner odpovie, ide do hĺbky, doloží. Manažér nie je pasívny prijímateľ dôkazu.
- Kvalitu drží spolu: partner + zásady + čestný dôkaz + manažér, ktorý to interaktívne posúdi a neprijme nič
  nedotiahnuté.

---

## 9. Rytmus — kedy partner hovorí a kedy robí (Manažér projektu 2026-07-03)

Rytmus závisí od FÁZY:
- **Príprava (pochopenie → zadanie → plán):** sústavná interaktívna komunikácia — tu ide neustály rozhovor,
  dohadujete sa. Nič sa nerobí „naslepo".
- **Programovanie (PO schválení plánu):** partner ide **v jednom kuse — ako Dedo.** Manažér nebabysituje každú
  úlohu; schválil SMER (plán), partner ho vykoná. Manažér to vidí naživo (plán sa plní), ale nemusí pri tom
  sedieť. **Partner sa ozve len pri naozajstnej nejasnosti / probléme / keď dokončí:**
  - manažér **prítomný** → ozve sa v rozhovore;
  - manažér má dole vľavo v sidebare **„Preč"** → ozve sa cez **Telegram.**
- **Kontrola (PO programovaní):** späť interaktívne — manažér prejde dôkaz, pýta sa, rozhodne.

**Token/session limity — nastaviteľná poistka (Manažér projektu 2026-07-03, zlepšovák):**
V *Nastavenia → Systém* pribudne parameter **„Stopnúť implementáciu pri prekročení X miliónov tokenov".**
- **0 / nič:** partner ide v jednom kuse (ako hore).
- **X > 0 (napr. 3 mil.):** keď spotreba implementácie prekročí X, partner **zastaví a pošle Telegram**, nech
  si manažér pozrie stav token-limitu a rozhodne, či pokračovať.

Výhoda: **dynamické** — keď Anthropic zmení tokenovú politiku, stačí zmeniť nastavenie, nič v kóde. Rieši presne
ten starý problém (Programátor písal všetko naraz, narazil na 3 session-limity, Manažér projektu musel dvakrát ručne
stopnúť) — elegantne a pod kontrolou manažéra. *(Doplnkovo pod kapotou aj tak navrhneme prácu odolne — úloha po
úlohe, čisté prenesenie cez hranicu session — nech sa nikdy nič nezlomí.)*

> Skúšobný režim: po schválení plánu → implementácia v jednom kuse (0), kontakt cez Telegram pri „Preč". Overiť
> v praxi.
