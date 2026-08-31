# v4.2.9 — previerka už neblokuje aplikáciu, ktorá je v poriadku

## Čo sa stalo

Stavba `nex-productcatalogs` prešla celým Programovaním — 4420 testov zelených,
overené z čistej kópie projektu. Manažér ju schválil a previerka pred vydaním ju
odmietla s hlásením:

> Aktualizácie chýba vo frontende: chýba navigácia na `/updates` v menu

**Tá položka v menu bola.** Aj stránka, aj odkaz naň.

## Prečo ju previerka nevidela

Kontrola hľadala odkaz na novinky **v troch presne určených tvaroch zápisu**. Tento
projekt si však všetky položky menu skladá jednou vlastnou pomocnou funkciou —
dvanásť položiek, všetky rovnako:

```
{item("✨", "Aktualizácie", "/updates")}
```

Cesta je tu **tretím údajom odovzdaným funkcii**, nie zápisom v tvare, ktorý
kontrola poznala. Preto ju minula.

Nebolo to prvýkrát. Ten istý zoznam tvarov sa už raz rozširoval — a znova
nestačil. Hádať, akými spôsobmi sa dá odkaz zapísať, nemá koniec: ďalší projekt
s vlastným pomocníkom by narazil znova.

## Čo sa zmenilo

Kontrola prestala hádať tvary a hľadá **cieľ**: cestu končiacu na `/updates`,
nech je odovzdaná akokoľvek.

Všetky poistky zostali:

- **samotné smerovanie sa za položku menu nepočíta** — inak by stránka
  dosiahnuteľná len cez adresu zakryla chýbajúce menu
- odkaz na inú stránku (`/updates-log`) sa nezaráta
- slovo „aktualizácie" v nesúvisiacom texte nestačí
- zakomentovaná položka nie je položka

## Prečo sme neopravili projekt

Dalo by sa jednu z tých dvanástich položiek prepísať tak, aby vyhovela kontrole.
Znamenalo by to **pokaziť projekt, aby vyhovel nástroju** — a nasledujúci projekt
by narazil rovnako.

Previerka, ktorá zastaví správne postavenú aplikáciu, je horšia než chyba, ktorú
stráži: stavba stojí a hľadá sa niečo, čo tam nie je.
