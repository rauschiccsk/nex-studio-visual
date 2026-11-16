# v4.150.0

**Ostré nasadenie na MAGER samo založí meno aplikácie v DNS a overí, že naozaj vedie na inštaláciu.** Aplikácie na
MAGERi sa otvárajú cez Tailscale menom v zóne icc.sk (napríklad skit-manager.icc.sk). Aby meno viedlo na MAGER, musí mať
v DNS vlastný záznam; doteraz ho pri novom zákazníkovi musel založiť človek ručne v Cloudflare. Bez neho meno zachytil
zastrešujúci záznam zóny a viedol na webhosting — a kokpit aj tak ohlásil „Nasadené“, lebo odpoveď webhostingu („403“)
považoval za odpoveď aplikácie.

Teraz kokpit pred spustením ostrej inštalácie na MAGERi overí každé jej meno vo verejnom DNS. Meno bez záznamu založí sám
— vedie na MAGER v Tailscale, bez proxy Cloudflare — a počká, kým ho verejné DNS ukáže; v hlásení nasadenia to povie
vetou „Meno … dostalo v Cloudflare záznam na stroj MAGER v Tailscale“. Záznam, ktorý vytvoril niekto iný a vedie inam,
neprepisuje: nasadenie vtedy nezačne a povie prečo. Po nasadení prejde overenie adresy, len keď meno vedie na MAGER;
inak povie, kam meno vedie. Samotnú aplikáciu sa overenie pýta priamo na MAGERi s jej menom a overeným certifikátom,
nie cez pamäť DNS na stroji kokpitu, ktorá môže ešte pár minút pamätať starú odpoveď.
