# v4.14.0 — Nasadenie už nevpisuje do tajomstiev zástupnú hodnotu

Toto je oprava bezpečnostnej diery, ktorú sme našli 6. septembra.

## Čo sa dialo

Keď aplikácia potrebuje tajomstvo, ktoré nasadenie nemá odkiaľ vziať, vpisovalo
doň **zástupnú hodnotu** — text, ktorý mal obsluhe povedať „toto ešte doplň".

Lenže tá hodnota je **konštanta napísaná v našom zdrojovom kóde**. A aplikácia,
ktorá má pravidlo „prázdne = zamknuté", považuje čokoľvek vyplnené za odomknuté.

Výsledok: testovacie nasadenie NEX ProductCatalogs pustilo dnu **kohokoľvek ako
ktoréhokoľvek používateľa**, keď do adresy zadal tú konštantu. Overili sme to
naživo na verejnej adrese — aplikácia odpovedala prihlásením.

**Vyplnená zástupná hodnota v bezpečnostnej premennej je horšia než prázdna.**

## Čo sa mení

Tajomstvo teraz **nikdy** nedostane zástupnú hodnotu. Dostane skutočnú náhodnú.

Nesie to rovnaký odkaz — „toto nikto nedodal" — ale **neotvára nič**. A tam, kde
sa tajomstvo musí zhodovať s iným systémom, to zlyhá **zamknuté**, kým ho obsluha
nenahradí.

Pri opätovnom nasadení sa už existujúce tajomstvo zachová, aby sa nerozbilo
funkčné prepojenie.

## Čo pribudlo navyše

Po nasadení dostaneš **zoznam premenných, ktoré nikto nedodal** a dostali náhodnú
hodnotu. Nič nie je otvorené — ale ak sa niektorá z nich musí zhodovať s iným
systémom, teraz vieš ktorá, namiesto hľadania.

## Kde zástupná hodnota zostáva

Tam, kde je neškodná: pri **nebezpečnostných** položkách, ktoré má obsluha
doplniť. Tie nič neodomykajú a aplikácia na nesprávnej hodnote radšej nahlas
spadne, než by bežala zle.
