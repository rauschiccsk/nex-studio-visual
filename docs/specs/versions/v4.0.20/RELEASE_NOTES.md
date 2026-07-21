# v4.0.20 — NEX Studio Visual

## Token-spúšťané aplikácie sa nasadia ako spustiteľné z NEX Managera — bez ručného zásahu

Aplikácie, ktoré sa spúšťajú cez NEX Manager (token-launch, ako NEX Inbox alebo NEX Shopify), musia svoje spúšťacie tokeny overovať **tým istým kľúčom, ktorým ich NEX Manager podpisuje**. Doteraz nasadzovač každému tajnému kľúču pridelil náhodnú (synthetic) hodnotu — čo je správne pre bežné tajomstvá, ale pre spúšťací kľúč to znamenalo, že sa **nikdy nezhodoval** s Managerom a appka sa z Managera nedala spustiť (hlásila neplatný token). Kľúč sa musel dopĺňať ručne.

Od tejto verzie:

- **Nasadzovač rozpozná token-spúšťaný modul** (podľa toho, že appka deklaruje `MANAGER_LAUNCH_SIGNING_KEY`) a **automaticky mu nadrôtuje spúšťací kľúč z párového NEX Managera** toho istého zákazníka — plus jeho deploy identifikátor. Manažér už nič neprepája; stačí kliknúť Nasadiť.
- **Modulový (privátny) session kľúč ostáva náhodný** a zachováva sa medzi nasadeniami (relácie nevypadnú pri redeploy). Ostatné tajomstvá sa správajú ako doteraz.
- Ak párový Manager ešte nie je nasadený, spúšťací kľúč ostane **prázdny** (token-launch je vypnutý a spúšťač to čisto odmietne) — nikdy sa nenastaví náhodná hodnota, ktorá by tiché rozbila každé spustenie.

Vďaka tomu sa token-spúšťaná aplikácia z dielne NEX Studia dá spustiť z NEX Managera hneď po nasadení cez cockpit — presne to, čo má zvládnuť manažér bez špecialistu.
