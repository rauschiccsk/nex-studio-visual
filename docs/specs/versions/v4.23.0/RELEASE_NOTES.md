# v4.23.0 — upozornenia idú s manažérom, ľudia sa volajú menom

## Upozornenia idú s manažérom

Zverenie projektu dovtedy presunulo **zodpovednosť**, ale nie **upozornenia**. Sú to
v systéme dva rôzne údaje. Prakticky to znamenalo, že projekt patril novému
manažérovi, ale keď sa stavba na niečo spýtala, cinklo to starému — a nový o svojom
vlastnom projekte nevedel, kým sa nepozrel do kokpitu.

Zmerané naživo hneď po prvom zverení: zodpovedný `tibi`, adresát upozornení `admin`.

Odteraz idú upozornenia s manažérom. Adresát pritom žije na **dvoch** miestach — v
databáze (odtiaľ šťuchá kokpit) a v `.env` projektu (odtiaľ hlási sám agent) — a
presúvajú sa obe. Presunúť len jedno by bola oprava polovice: kokpit by šťuchal
správne, agent by hlásil ďalej starému.

### Keď nový manažér nemá kam

Upozornenia sa presunú len tomu, kto má zapísaný Telegram. Bez neho by neprišli
**nikomu** — z „chodia nesprávnemu človeku“ by sa stalo „nechodia vôbec“, čo je
horšie, lebo je to tichšie. V takom prípade zostanú pôvodnému a kokpit to povie
priamo pri zverení, spolu s tým, čo s tým robiť.

Rada „doplň mu Telegram a zver projekt znova“ aj naozaj funguje: opakované zverenie
tomu istému človeku síce nepridá riadok do histórie (nič sa nezmenilo), ale adresáta
upozornení dorovná. Bez toho by bola jediná cesta späť zásah do databázy.

### Po zverení sa už neodchádza preč

Obrazovka predtým hneď skočila na zoznam projektov. Zveriť projekt smie jedine admin
a ten vidí všetky, takže sa mu nič nezavrelo — odchod len zmietol správu o tom, čo sa
stalo, skôr než sa dala prečítať.

## Ľudia sa volajú menom

V ponuke „komu zveriť projekt“ aj v histórii presunov stálo prihlasovacie meno
(`tibi`). Manažér vyberá človeka, nie účet. Teraz je všade **Meno a Priezvisko**;
prihlasovacie meno ostáva len tam, kde meno v konte vyplnené nie je.

Panel navyše konečne povie, **komu projekt patrí teraz** — dovtedy ponúkal, komu ho
zveriť, ale odpoveď na prvú otázku, ktorú si pri ňom človek položí, v ňom nebola.

Tá istá skladačka mena bola rozpísaná na troch miestach; je z nej jeden spoločný
pomocník. Štvrtá kópia by bola presne to miesto, kde sa raz jedna zmení a ostatné nie.

## Stráže

Panel zverenia bol dovtedy na obrazovke **bez jedinej stráže** — existujúce atrapy
slúžili len na to, aby sa stránka vykreslila. Pribudlo jedenásť stráží, každá overená
červená pred opravou.
