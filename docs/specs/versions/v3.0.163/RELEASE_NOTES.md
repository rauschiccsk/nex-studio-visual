## v3.0.163 — Oprava: opätovné nasadenie už nezhodí smerovanie

Pri **opätovnom nasadení** (redeploy) mohlo nasadenie omylom zapísať do smerovania nesprávnu adresu (internú docker hodnotu namiesto skutočnej domény zákazníka), čím sa appka stala zvonku nedostupnou — hoci kontajnery bežali. Príčinou bola zámena internej premennej pri prenášaní nastavení z bežiacej inštancie. Opravené: verejná adresa sa už vždy odvodí správne a prenos ostatných nastavení funguje ako dovtedy. Doplnený poistný test, aby sa to nezopakovalo.
