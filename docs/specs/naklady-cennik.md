# Náklady podľa cenníka Anthropic z Claude Code (ICCINT-168, v4.43.0)

Schválil Director 06.10.2026 („Áno, súhlasím, môžeš začať.") po troch spresneniach návrhu; priebeh a merania sú
v tikete ICCINT-168.

## Prečo

Náklady ukazovali len zlomok skutočnej spotreby agentov.

1. **Vyrovnávacia pamäť chýbala.** Agent pri každom kroku znova číta celý doterajší rozhovor; Claude Code ho číta
   z vyrovnávacej pamäte a aj to sa platí. Kokpit z výsledku behu bral len vstup a výstup. Dedo Home 0.1.0:
   kokpit zapísal 6,9 milióna výstupných tokenov, záznam sedenia má navyše 2,27 miliardy prečítaných a 17,5 milióna
   zapísaných do pamäte. Uzavreté ťahy stáli podľa samotného Claude Code 1 493,53 $, Náklady ukazovali asi 172 €.
2. **Ručné ceny nesedeli** s modelmi (Haiku výstup 3 namiesto 5, Sonnet 5.5 vstup 3 namiesto 2, Opus 5.5 4/20,
   nie 5/25) a ceny pamäte sa líšia podľa modelu — Opus 5.5 číta pamäť za 5 % vstupu, Haiku a Sonnet za 10 %.
3. **Prerušený ťah nemal cenu** (vypršaný čas, pád, zastavená odpoveď Poradcu).

## Čo Claude Code o cene hovorí (zmerané 06.10.2026, verzia 2.1.290)

- Výsledok behu (`result`) nesie `total_cost_usd` a `modelUsage` — pri pokračovaní sedenia (`--resume`) sú to
  **súčty za celé sedenie**, nie za beh. Pole `usage` je za beh, ale bez rozpisu po modeloch.
- Na konci každého **dokončeného** behu zapíše Claude Code do záznamu sedenia riadok `cost-state` so súčtom po
  modeloch (tokeny štyroch druhov aj cena). Cena behu = rozdiel súčtov.
- **Prerušený** beh `cost-state` nezapíše a v súčte chýba. Jeho dokončené správy sú v zázname s konečnou
  spotrebou (platí posledný zápis správy); pomocníci v `<sedenie>/subagents/*.jsonl`.
- Priebežné správy `stream-json` nesú spotrebu zo začiatku správy (4 výstupné tokeny namiesto 2 983) — na cenu
  sa nepoužívajú.

## Ako to kokpit robí

| čo | kde |
|---|---|
| spotreba ťahu zo záznamu sedenia — súčet pred ťahom a po ňom, na každej ceste von (aj pri chybe) | `backend/services/usage_ledger.py`, `claude_agent._run_turn`, `poradca/runner.py` |
| spotreba ťahu po modeloch (`payload.usage.parts`: vstup, výstup, čítanie a zápis pamäte, cena Claude Code alebo `null`) | `orchestrator._DispatchMetrics`, `poradca_messages.usage` |
| cenník každého modelu vyčítaný zo zaplatených ťahov (najmenšie štvorce, zaokrúhlenie na najhrubší krok, ktorý ceny Claude Code zopakuje do 0,5 % súčtu a 2 % ťahu) | `backend/services/model_pricing.py`, tabuľka `model_prices` (migrácia 104) |
| kurz ECB (referenčný), stiahnutý raz, keď cenník vznikne, uložený k nemu s dátumom | `model_pricing.ecb_rate` |
| Náklady: tokeny × cenník platný v čase ťahu × kurz toho cenníka, v celých eurách zaokrúhlených **hore**; súčty sčítavajú zaokrúhlené riadky | `backend/services/metrics.py` |
| čo sa oceniť nedá, je pomenované pri riadku (`unpriced`) a súčet je označený ako neúplný (`agent_cost_complete`) | `metrics.UNRECORDED_REASON` a ďalšie |
| pri každej verzii aj projekte „Cenník, ktorým sme počítali" — ceny v $ a €, kurz, dátum, zdroj | `price_list`, `MetricsPage.tsx` |
| doplnenie starších ťahov zo záznamov sedení (výkaz nanečisto, zápis na `--apply`) | `backend/services/usage_backfill.py`, `python -m backend.scripts.backfill_usage` |

**Cena odpovede Poradcu** ide tým istým cenníkom, zaokrúhlená hore na celé centy (pri jednej odpovedi by celé
eurá klamali).

### Spresnenia po nezávislej kontrole (06.10.2026)

- **Ťah s rozrobenou prácou** (vypršal či spadol po zápise zmien) nezapíše inú správu než upozornenie o rozrobenej
  práci — spotreba ide doň (`orchestrator._audit_lost_work`), po ťahoch (`metered_turns`): ten istý ťah pri
  opakovaní svoj zápis nahradí, ďalší ťah toho istého behu stavby pribudne.
- **Ťah bez akejkoľvek zaznamenanej spotreby** (`usage` je `null`, ale ťah bežal) je „nevyčíslený" — jeho cena nie
  je nula. Ťah, ktorého záznam sa prečítal a nič neminul, má nulu.
- **Vyhľadávanie na webe** Claude Code účtuje zvlášť od tokenov (0,01 $). Ťah s ním cenník tokenov neučí — vstup
  by cenu pohltil (Haiku 1,55 $ namiesto 1 $); cena jedného vyhľadávania sa vyčíta zvlášť (`web_search_usd`).
- **`hasUnknownModelCost`**: keď Claude Code cenu modelu nepozná, jeho číslo sa za cenu nevydáva a cenník neučí.
- **Kurz ECB** sa pri jednej kontrole stiahne najviac raz a po neúspechu sa 10 minút neskúša (Náklady nečakajú).
- **Projekt nikdy neukáže menej než súčet verzií**: každá verzia zaokrúhľuje nahor sama, projekt je súčet
  zaokrúhlených verzií plus (nahor) to, čo nepatrí žiadnej verzii.
- **Doplnenie histórie**: zhrnutie pri zhustení rozhovoru (`isCompactSummary`) nie je nové zadanie (inak by sa časť
  behu zarátala dvakrát — Dedo Home +169-tisíc výstupných tokenov); behy sa priraďujú ku VŠETKÝM ťahom vrátane
  doplnených, takže opakované spustenie nič nezaráta dvakrát; ťah, ktorý zlyhal bez spotreby, dostane spotrebu len
  do ceny (vstup a výstup ostávajú nulové); odpovede Poradcu jedného rozhovoru sa priraďujú spolu.

## Čo sa zámerne nemení

- **Ľudský čas a limit tokenov stavby** ostávajú na vstupe + výstupe — čítanie z pamäte nie je práca, ktorú by
  robil človek.
- **Ručne zadané externé náklady** majú len vstup a výstup; ocenia sa najnovším cenníkom zvoleného modelu (rodiny).
- **Ťah bez záznamu sedenia** (spred v4.43.0 a nedoplnený) sa neodhaduje zo vstupu a výstupu — bez pamäte by bol
  podcenený zhruba desaťnásobne; Náklady ho pomenujú „nevyčíslené".

## Čo zmizlo

- Panel „Ceny modelov" v Nastaveniach a kľúče `api_price_*` (migrácia 104 ich zmaže; pred zmazaním boli Haiku 1/3,
  Sonnet 3/10, Opus 5/25, neznámy model 10/50 € za milión). Director: „je zmetkujúce ak niečo tam je uvedené
  a v skutočnosti sa používa niečo iné."
- Premenné `API_PRICE_*` v nastaveniach backendu.

## Hranice

- Správa rozpísaná v okamihu prerušenia má v zázname nuly — jej doterajší výstup sa zistiť nedá.
- Ťah, ktorý zabil reštart backendu, sa nezapíše vôbec (tak ako doteraz); jeho spotreba je len v zázname sedenia.
- Model sa ocení, až keď má aspoň štyri nezávislé zaplatené ťahy; dovtedy „nevyčíslené".
- Cenník je viazaný na úplné meno modelu; keď Anthropic zmení cenu toho istého modelu, vznikne nový cenník od prvého
  ťahu, ktorý starý nezopakuje, a starý ostáva pre staršie ťahy.
