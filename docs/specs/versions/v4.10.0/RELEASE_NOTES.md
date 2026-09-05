# v4.10.0 — Nasadená aplikácia sa už dá aj otvoriť

Nasadenie NEX ProductCatalogs do UAT prešlo, aplikácia bežala — a otvoriť sa nedala.
Odpovedala, že sa má otvoriť z NEX Managera, lenže Manager o nej nevedel. Nebolo na
čo kliknúť.

## Prečo

Aplikácia, ktorá nemá vlastné prihlasovanie, potrebuje od Managera **tri veci**:
podpisový kľúč, jeho adresu a **vlastný kľúč**, ktorým sa Managera pýta, kto je
prihlásený. Nasadenie doteraz zapojilo **len prvú**.

Ten tretí kľúč vzniká až pri registrácii v Manageri. A registrovať mohol jedine
prihlásený človek s právom správcu — takže automaticky nasadená aplikácia zostala
neviditeľná. Sliepka a vajce: kľúč dostane až po registrácii, ale registrovať sa
bez neho nedalo.

## Čo sa mení

**Nasadzovací systém má vlastný kľúč.** Nie prihlásenie človeka a nie heslo správcu —
vlastný kľúč, presne ako ho už dnes majú registrované aplikácie, keď Managera volajú.
Nikto sa za nikoho nevydáva.

Pri nasadení sa teraz aplikácia v párovom Manageri **sama zaregistruje** a dostane
všetky tri veci naraz. Po nasadení je dlaždica na mieste a dá sa kliknúť.

## Čo sa nemení — a to je podstatné

**Kto smie aplikáciu používať, rozhoduje naďalej len správca.** Ten kľúč vie modul
založiť a upraviť, ale **prístup k nemu nikomu nedá** — to zostáva na človeku.

Manager, ktorý taký kľúč nedostal, sa správa presne ako doteraz.

Registrácia je **best-effort**: keby Manager náhodou nebežal, nasadenie sa nezruší.
Aplikácia sa nainštaluje a dostaneš upozornenie, že dlaždica zatiaľ chýba.

## Drobnosť, ktorá stála za overenie

Volanie ide **vnútri servera**, medzi kontajnermi. Overili sme, že verejná adresa
vedie cez Cloudflare — teda von a späť. Pre operáciu medzi dvoma kontajnermi na tom
istom stroji by to znamenalo posielať tajný kľúč do sveta zbytočne.
