# v4.43.28

**Pri zákazníkovi zadáš nastavenia, ktoré aplikácia od neho potrebuje.** Aplikácia v nastaveniach zdrojového kódu
označí, čo zadáva zákazník, napríklad e-mail a heslo prvého správcu alebo schránku, z ktorej číta. V sekcii
Zákazníci sa potom pri každom zákazníkovi ukážu presne tieto polia aj s vysvetlením. Heslá sa uložia do trezoru
prístupov a späť sa už neukážu, pri uloženom hesle je len „uložené“. Pri nasadení kokpit hodnoty odovzdá
aplikácii aj s presnou adresou, na ktorej inštalácia beží. Zoznam zákazníkov povie, čo ešte treba doplniť.

**Nasadenie bez prístupu sa nespustí.** Ak by heslo správcu nasadenej aplikácie dostalo náhodnú hodnotu, ktorú
nikto nepozná, alebo ak zákazníkovi chýba povinné nastavenie, nasadenie sa zastaví skôr, než sa čokoľvek zmení,
a kokpit povie, čo urobiť. Pole „Tajný kľúč“ sa ukazuje len pri aplikácii, ktorá ho naozaj používa.

**Fakturácia na jednom mieste.** Na stránke Náklady pribudla záložka Fakturácia. Ukáže všetky verzie projektu
od najnovšej a pri každej, či je jej súpis dodávky vydaný, pripravený na vydanie, alebo čaká na rozhodnutie
(pri rýchlej oprave, či opravovala chybu, alebo robila zmenu). K tomu sumu a hore súčty vydaného a ešte
nevydaného. Súpis ktorejkoľvek verzie otvoríš a vydáš priamo tam, bez prepínania verzie. Doteraz bolo vidieť len
súpis vybranej verzie a ostatné verzie vyzerali, akoby zmizli.
