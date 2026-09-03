# v4.7.0 — pravidlá agenta prestávajú zastarávať

Druhého septembra som doplnil agentovi nové pravidlo — kedy má pri rýchlej oprave zastaviť
a spýtať sa ťa. Zapísal som ho do šablóny a potom si overil, kam sa dostalo.

**Nikam.**

## Čo sa dialo

Pravidlá agenta sa napíšu **raz, pri založení projektu.** Potom si ich engine pri každom
spustení agenta prečíta z tej kópie — a tá sa už nikdy neobnoví.

Šablóna má pritom od augusta jediného vlastníka. Vyzeralo to teda, že keď zlepším pravidlá,
zlepším ich všade. V skutočnosti sa každé zlepšenie zastavilo pred projektmi, ktoré už existovali.

Namerané v ten deň:

| projekt | pravidlá pochádzajú z | riadkov |
|---|---|---|
| nex-payables | 6. júla | 230 |
| nex-shopify | 15. júla | 253 |
| nex-websites | 24. júla | 282 |
| nex-productcatalogs | 21. augusta | 326 |
| *šablóna* | | *304* |

Každý agent podľa iných pravidiel — a nikde to nevidno.

Najhoršie na tom nebolo to zaostávanie samo. Bolo to, že **budúce opravy by sa mlčky nedoručili.**
Opravil by som príčinu správne a opravil by som ju len pre projekty, ktoré ešte neexistujú.

## Čo sa mení

**Keď sa stavba rozbehne, agent dostane pravidlá zo šablóny.** Vždy tie aktuálne, bez toho, aby
si na to niekto musel spomenúť.

Obnovujú sa len samotné pravidlá. Nastavenia, dôveryhodnosť adresára ani ostatné veci sa
netýkajú — zastarávajú pravidlá, tak sa obnovujú pravidlá.

Keď už charta sedí, nezapisuje sa nič.

## Prevzatých projektov sa to netýka

Projekt, ktorý si do NEX Studia priviedol už hotový, si drží **vlastné pravidlá** — tie si písal
ty a nie sú naše, aby sme ich prepisovali.

Doteraz sa to nedalo spoľahlivo rozlíšiť: či bol projekt prevzatý, sa vypočítalo pri jeho založení,
raz použilo a zabudlo. Engine to pri spúšťaní agenta nemal odkiaľ vedieť.

**Teraz si to projekt pamätá.** Prevzatý projekt sa pri obnove preskočí.

## Čo sa nezmení samo

Tri projekty v kokpite dostanú nové pravidlá **pri najbližšej stavbe**, nie hneď pri nasadení.
Dovtedy bežia podľa tých svojich.

A `nex-payables` patrí staršiemu kokpitu, ktorý je iný program — tam táto oprava nedosiahne.

---

*Prečo stredná číslica: mení sa správanie, na ktoré sa dá spoľahnúť — agent odteraz pracuje podľa
aktuálnych pravidiel. Nie je to oprava chyby v tom, čo appka robí, ale v tom, čo sľubuje.*
