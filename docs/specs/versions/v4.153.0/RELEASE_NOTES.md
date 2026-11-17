# v4.153.0

**Živý náhľad Vizuálu sa po preinštalovaní knižníc nevypne a dá sa kedykoľvek spustiť znova.** Keď Staviteľ počas
Vizuálu nanovo nainštaloval knižnice aplikácie, náhľad zostal prázdny a z kontrolného zoznamu sa nedalo nič otvoriť.
Náhľad si teraz pripravené knižnice drží mimo projektu, takže mu ich inštalácia nezoberie. Tlačidlo „Spustiť náhľad
znova“ je aj pri bežiacom náhľade a náhľad vždy vytvorí nanovo.

**Nasadiť sa dá len verzia, ktorej kód je naozaj ten, čo by sa nasadil.** Ponuka „Nasadiť verziu“ ponúkala aj starú
vydanú verziu, hoci by sa postavil aktuálny, ešte neoverený kód. Teraz sa staršia verzia neponúkne, keď v projekte už
pribudla práca na novšej. Obrazovka nasadenia vtedy povie, ktorá verzia sa nasadí, keď bude hotová, a vedie na ňu.
„Over znova“ sa pri takej verzii neponúka a zákazníkovi, u ktorého beží novšia verzia, sa staršia nepredvolí.

**Viacjazyčné aplikácie majú Aktualizácie aj Ako začať v jazyku človeka.** Poznámky k vydaniu, ktoré kokpit skladá
sám, vznikajú vo všetkých jazykoch aplikácie: preklad opisu každej časti plánu píše Staviteľ spolu s plánom. Prvé
nasadenie na PROD presunie poznámky vo všetkých jazykoch a nová aplikácia má návod Ako začať v každom jazyku aj
v nasadenej aplikácii (šablóna nex-app-kit 0.16.1).
