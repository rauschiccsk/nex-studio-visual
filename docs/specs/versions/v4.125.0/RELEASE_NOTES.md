# v4.125.0

**Verifikácia vyskúša nasadenie skôr, než je verzia hotová.** Keď aplikácia prejde skúškou spustenia, kokpit zapíše
inštaláciu presne tým kódom, akým nasadzuje — pre nového zákazníka aj pre každú existujúcu inštaláciu UAT projektu —,
postaví jej obrazy z overeného commitu, spustí ju a overí, že backend odpovedá. Skúšobná inštalácia má vlastné meno,
nemá adresu zvonku ani porty, nevidí ostré služby, má vlastné prázdne dáta a vymyslené heslá; potom sa zmaže aj s obrazmi.
Keď skúška zlyhá, je to nález Verifikácie s vetou, ktorá inštalácia a prečo — chyba sa tak ukáže pred „Nasadiť“, nie až
na UAT. Overené naživo: inštalácia NEX Websites zo stavu 09.11.2026 ráno by touto skúškou neprešla presne tou chybou,
na ktorej vtedy zlyhalo nasadenie. Neoverené ostávajú dáta zákazníka, stroj MAGER a prepojenie s NEX Managerom
zákazníka — tie overuje kokpit po skutočnom nasadení.
