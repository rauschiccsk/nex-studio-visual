# v4.28.3 — do prevzatého projektu sa už nescaffolduje

Tretí pokus o prevzatie NEX Managera skončil takto:

> Filesystem bootstrap failed: ERROR: `/opt/projects/nex-manager/CLAUDE.md` already exists
> (use --force to overwrite)

## Čo sa dialo

NEX Studio pri zakladaní projektu rozbalí do priečinka šablónu. Pri prevzatí to nedáva
zmysel — projekt tam už je — a šablóna sa správne odmietla prepísať cudzí súbor.

Príznak „prevzatý projekt" v systéme **bol už predtým** a pravidlá agenta ho rešpektovali.
Zakladanie ho však ignorovalo a šablónu rozbaľovalo vždy. Prevzatie cez kokpit teda nikdy
nemohlo prejsť: existovala len jeho polovica.

## Čo sa pri prevzatí vynecháva

Tri veci, každá z iného dôvodu:

- **rozbalenie šablóny** — do hotového projektu niet čo rozbaliť;
- **odoslanie do repozitára** — repozitár existuje a je odoslaný; tlačili by sme doň
  miestny stav, o ktorý nikto nežiadal;
- **kroky po založení** (CI, ochrana vetvy, skúšobné spustenie) — bežiacemu projektu by
  prepisovali nastavenia, ktoré si niekto nastavil sám. A skúšobné spustenie robí
  `docker compose down -v`, čo by živému projektu **zmazalo databázu**.

Pravidlá agenta sa dopĺňajú aj tak — bez nich by sa v projekte nedal spustiť agent, a to je
práve dôvod, prečo sa preberá.

Vynechanie sa manažérovi **povie**: prevzatý projekt si CI a ochranu vetvy drží vlastnú.

## Stráže

Nielen na text kódu, ale na správanie: priečinok s obsahom sa nesmie prescaffoldovať,
a priečinok, ktorý ešte neexistuje, sa scaffoldovať **musí** — inak by nový projekt vznikol
prázdny. Obe strany overené červené.
