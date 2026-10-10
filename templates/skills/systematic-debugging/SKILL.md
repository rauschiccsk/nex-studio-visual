---
name: systematic-debugging
description: Ladenie od príčiny — najprv zreprodukovať a vysvetliť, až potom meniť kód. Použi, keď skúška padne, CI či stavba zlyhá, migrácia skončí chybou, aplikácia sa správa inak, než má, alebo Manažér hlási „nefunguje to“ — a vždy predtým, než chybu eskaluješ ako otázku alebo framework_issue.
---

# Ladenie od príčiny

**Žiadna zmena kódu, kým chyba nie je pochopená.** Skúšanie „kým to nezačne fungovať“ je cesta, ktorou sa do kódu
dostávajú tiché chyby.

## 1. Zreprodukuj

1. Zachyť **presný** výstup chyby (stack trace, HTTP kód, padnuté tvrdenie, konzola prehliadača, riadok logu) —
   doslovne, neprerozprávaj ho.
2. Nájdi **najmenší spúšťač** — jedna adresa, jedna skúška, jeden krok.
3. Over, či padá **vždy**, alebo zapíš vzor nestability (padá 2 z 5 behov…).

Keď sa chyba zreprodukovať nedá, neopravuj ju — pridaj meranie, ktoré ju nabudúce zachytí.

## 2. Nájdi miesto

1. Prečítaj súbory, kam ukazuje stack trace — nič nepredpokladaj. `git log -p <súbor>` ukáže nedávne zmeny.
2. Keď to predtým fungovalo, hľadaj zlom delením histórie (`git log --oneline <súbor>`, commit pred zmenou).
3. Miesto potvrď cieleným pokusom (dočasný výpis pri podozrivom riadku); pred commitom ho odstráň.

## 3. Vysvetli príčinu

1. Napíš príčinu jednou vetou („automatické ukladanie sa nespustilo, lebo funkcia držala starý stav — commit `<hash>`“).
2. Pomenuj **druh** chyby (zastaraný stav, súbeh, `None`, nesúlad typu v SQL, chýbajúca migrácia, port…).
3. Ktoré pravidlo sa porušilo a **prečo ho žiadna skúška nechytila** — chýbajúca skúška je prvý kus opravy.

## 4. Oprav a zabráň návratu

1. Skúška, ktorá chybu dokáže (červená — zručnosť `tdd`), potom najmenšia oprava (zelená).
2. Pozri susedov: tá istá chyba býva aj v súbežnom module.
3. V tele commitu: spúšťač, príčina jednou vetou, obnovené pravidlo.

Neprepisuj celý súbor „keď už som tu“ — meň len to, čo chyba vyžaduje. Opravu nehlás, kým si nevidel skúšku prejsť
z červenej na zelenú. Obchádzku namiesto opravy príčiny nerob.

## Známa príčina: zastaraný zámok verzií (`nex-shared`)

Keď stavba alebo CI zlyhá na závislosti (chýbajúci export, nezhoda verzie spoločnej knižnice), najprv over
**skutočnú** príčinu: či zámok verzií (`package-lock.json`) sedí so zoznamom želaných verzií (`package.json`) —
deklarovaný tag **aj** rozriešený commit (porovnaj `nex-shared#vX.Y.Z` v oboch a rozriešený SHA s
`git ls-remote ... refs/tags/vX.Y.Z`). Najčastejšia príčina je **zastaraný zámok** (drží starý commit). Vtedy ho
**oprav sám** — rozrieš znovu (`rm package-lock.json && npm cache clean --force && npm install`) — a pokračuj;
je to mechanická oprava, **nie rozhodnutie pre Manažéra**.

## Keď je príčina mimo projektu

Knižnica tretej strany, databáza, konfigurácia: zreprodukuj ju na hranici (najmenší skript, ktorý zavolá
knižnicu) a vysvetli — známa chyba, zlá konfigurácia u nás, nesúlad verzií? Príčina vo vnútri NEX Studia
(orchestrátor, overenie, nasadenie) nie je tvoja — nahlás pozorovateľné fakty a zastav (charta, „Mašinéria
NEX Studia nie je tvoj pruh“).
