# v4.0.80 — desať závažných nálezov z hĺbkového auditu

Prvá z troch dávok opráv po komplexnej previerke kokpitu. Dve z týchto chýb boli
čerstvé — vznikli v ten istý deň, ako oprava, ktorá ich vyrobila.

## Kokpit už nezmaže priečinok, ktorý sám nevytvoril

Dvakrát tá istá chyba a obe rovnako: rozhodovalo sa podľa **toho, kde priečinok
leží**, nie podľa toho, **kto ho vytvoril**.

- Keď zakladanie projektu zlyhalo, kokpit priečinok zmazal. Keďže zakladanie
  úplne nového projektu sa medzitým odmieta, jediná cesta, ktorá tam viedla, bolo
  **prevzatie existujúceho projektu** — takže jedno zlyhanie pri odosielaní na
  GitHub by zmazalo celý zdrojový kód. Poistka overovala len to, že cesta leží
  pod `/opt/projects`, čo platí rovnako o každom tvojom projekte.
- Zakladanie chárt agentov prepisovalo `CLAUDE.md` a maralo priečinky s pravidlami
  — a to na **úspešnej** ceste, nie pri chybe. Pri prevzatom projekte sú to tvoje
  vlastné, ručne písané pravidlá.

Obe poistky teraz overujú pôvod. Prevzatý projekt sa nechá presne tak, ako sa našiel.

## Bezpečnostný profil agenta bol mŕtvy papier

Zákazy ako „nesmieš pretlačiť do gitu nasilu", „nesmieš zahodiť rozrobenú prácu"
alebo „nesmieš si prepísať vlastné pravidlá" boli zapísané do súboru, ktorý
Claude vôbec nečíta. **Agent teda pri každej stavbe bežal s plne schválenými
právami a všetko to smel.**

Nespoliehal som sa na dohady — spustil som skúšky priamo v kontajneri a zistil,
ktorou cestou zákaz naozaj platí. Pritom vyšli najavo ešte dve chyby v zápise
pravidiel: absolútne cesty potrebujú dvojitú lomku, inak nesedia na nič, a
polovica pravidiel bola napísaná tvarom, ktorý sa na súbory neaplikuje vôbec.

## Zakladanie prestalo klamať o sebe samom

Ktorýkoľvek krok po založení mohol zlyhať a kokpit aj tak ohlásil hotový projekt.
Zelený nápis nad projektom, ktorému sa nikdy nenastavila automatická kontrola.
Kroky ostávajú „najlepšou snahou" a zakladanie nezastavia — ale výsledok sa teraz
dostane na obrazovku a nápis povie, čo sa nedokončilo, namiesto zelenej fajky.

Neúspešné zakladanie tiež nechávalo po sebe priečinok, ktorý ten názov projektu
zablokoval navždy — a ten najpravdepodobnejší bod zlyhania bol jediný, ktorý po
sebe neupratoval. A obrazovka nového projektu si z chyby nechala len prvú vetu,
takže každé zlyhanie znelo ako „skús to o chvíľu znova".

## Latka pri vydaní sa už nedá obísť mlčaním

Kto nedeklaroval nič, nemusel nič dokázať — prázdna deklarácia ticho prešla a
kontrola sa zmäkla na „aspoň jeden test prebehol". Verzia, ktorá nepomenuje, čo
má predviesť, nie je málo riziková, ale **neoveriteľná**. Deklarácia je povinná.

## Ďalšie

- Zmazanie zákazníka zmazalo aj históriu nasadení — presne tie záznamy, o ktoré
  sa opiera poistka rozhodujúca, či sa projekt smie zmazať.
- Runbook Audítora čítal návratový kód za rúrou do `tee`, ktorý vracia nulu vždy.
  Neúspešné zostavenie sa preto zapísalo ako PASS.
