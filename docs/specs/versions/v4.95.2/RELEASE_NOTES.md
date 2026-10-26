# v4.95.2

**Kokpit už nedáva projektom ani AI Agentovi nič zo svojho nastavenia.** Procesy, ktoré kokpit spúšťa pre projekt —
skúška po spustení, skúška pri založení, nasadenie a skript skúšky pred vydaním — dostanú len systémové premenné a to,
čo im kokpit odovzdá zámerne. Agent mimo pieskoviska (terminál agenta, ťah bez pieskoviska) navyše len svoj prístup ku
Claude, čítací token GitHubu a adresy Znalostnej bázy. Doteraz išlo ďalej takmer celé prostredie kokpitu vrátane
pripojenia k jeho databáze a kľúča, ktorým podpisuje prihlásenia. Prejavilo sa to pri Author Asistentovi 0.1.0:
skúška po spustení dala aplikácii pripojenie k databáze kokpitu namiesto jej vlastného a Verifikácia hlásila chybu,
ktorá v projekte nebola.
