# v4.0.18 — NEX Studio Visual

## UAT nasadenie rešpektuje predvolené nastavenia aplikácie

Pri nasadení do UAT sa stalo, že aplikácia **spadla hneď po štarte** — nasadzovač jej do niektorých nastavení vložil interný zástupný symbol (`__UAT_SYNTHETIC__`) aj tam, kde má compose jasne určenú predvolenú hodnotu (napr. „zdroj dát = mock"). Aplikácia, ktorá si tie hodnoty kontroluje, taký text odmietla a nenaštartovala.

Od tejto verzie:

- **Nasadzovač rešpektuje predvolené hodnoty** z konfigurácie (`${PREMENNÁ:-predvolené}`) pri bežných nastaveniach — použije `mock` / `fake` a podobne, nie zástupný symbol.
- **Zástupný symbol ostáva len pre tajné údaje** (heslá, tokeny) a premenné bez predvolenej hodnoty — teda tam, kde ho manažér naozaj musí doplniť.

Vďaka tomu sa appka v UAT spustí správne bez ručného zásahu.
