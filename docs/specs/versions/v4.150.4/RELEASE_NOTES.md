# v4.150.4

**Povýšenie šablóny aplikácie funguje aj pri projekte, ktorý má vo svojom priečinku staré pracovné súbory.** Pri NEX
Manageri povýšenie na novú šablónu stále zlyhávalo: nástroj, ktorým kokpit zapisuje zámok závislostí servera, si
vzal starú kópiu šablóny, ktorá ležala v projekte mimo gitu, a zapísal do zámku starú verziu. Teraz kokpit zámok
prepočíta v čistej dočasnej kópii projektu a do projektu vráti len hotový zámok.

**Keď povýšenie šablóny zlyhá, hláška povie, ktorý krok to bol a čo našiel** — napríklad, že zámok servera zapísal
inú verziu, než má, alebo že verziu šablóny sa nepodarilo stiahnuť z GitHubu. Doteraz bola pri každej príčine tá istá
veta s radou overiť GitHub.
