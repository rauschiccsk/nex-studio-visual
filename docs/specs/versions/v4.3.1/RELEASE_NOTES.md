# v4.3.1 — hlásenia hovoria, čo sa naozaj zmeralo

Dve vety, ktoré zneli upokojujúco a nič nehovorili. Obe opravené.

## Kontrola už povie, čo videla

Po každej oprave stálo na obrazovke to isté:

> Kontrola po oprave — appka sa spustila.
> *Technický detail:* `app booted + responds`

Zakaždým rovnako, po anglicky, bez ohľadu na to, čo sa nameralo. Manažér sa
oprávnene pýtal, či to má byť zmysluplné hlásenie.

Kontrola pritom v tej chvíli vie dosť — ktoré služby odpovedali, čo dobehlo
čisto a **čo v tom čase ešte bežalo**. Všetko to zahadzovala.

Odteraz povie napríklad:

> Appka nabehla a odpovedá. Odpovedali: backend, frontend. Dobehli čisto:
> migrate. **V tom čase ešte bežali: test — ich výsledok táto kontrola
> neposudzuje.**

Tá posledná veta je najdôležitejšia. Práve ona chýbala v deň, keď kontrola
prešla nad testami, ktoré sa **nikdy neskončili** — a „kontrola prešla" sa
prirodzene číta ako „testy prešli".

Keď niet čo dodať, veta zostáva krátka. Žiadny šum tam, kde nie sú novinky.

## Po vypršaní času už obrazovka neklame o tvojej práci

Keď agentovi vypršal čas, na obrazovke stáli **dve vety, ktoré si protirečili**:

| kde | čo hovorila |
|---|---|
| v rozhovore | *žiadna zmena nezistená* |
| pokyn pod tým | *hotové zmeny sú zapísané, môžeš pokračovať* |

Pravdivá bola tá prvá. Štyridsať minút práce bolo preč a obrazovka na
najviditeľnejšom mieste ubezpečovala, že je uložená.

Systém si pritom zapísané zmeny **počíta** — len sa ho na to tá druhá veta nikdy
nespýtala. Teraz sa pýta:

- nič sa nezapísalo → *nezapísala sa žiadna zmena, takže rozpracovaná práca je
  preč a ďalší ťah začína od predošlého stavu*
- niečo sa zapísalo → *môžu byť zapísané zmeny (N commitov) — over `git log`*
- nedá sa zistiť → *nevieme, či sa niečo stihlo zapísať*

Rovnako je opravené hlásenie pri výpadku spojenia — nieslo tú istú nepravdu.
