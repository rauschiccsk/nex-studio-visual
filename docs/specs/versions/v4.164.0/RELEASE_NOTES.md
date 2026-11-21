# v4.164.0

**Zásobník je preč — jediným zdrojom práce pre verziu sú Plane tikety.** Tiket zaradíš do verzie priamo v záložke
Plane tikety: vyberieš verziu a stlačíš „Zaradiť do verzie“. Ponúknu sa len verzie, ktoré ešte len začínajú alebo sú
v Príprave. Staviteľ dostane tikety verzie v Príprave ako súčasť zadania, aj tiket zaradený počas Prípravy. Kým ho
Staviteľ nedostal, tiket vyradíš tlačidlom „Vyradiť z verzie“. Po prvom úspešnom nasadení verzie do PROD kokpit
uzavrie práve tikety, ktoré verzia rieši, každý raz a s komentárom. Súvisiaci tiket, ktorý verzia nerieši, ostáva
otvorený. Tiket, ktorý rieši niektorá verzia, má odznak „Vo verzii vX“.

**Nápady, Poradca a Poznámky zakladajú tikety.** „Pridať do projektu“ v Nápadoch založí tiket projektu v Plane.
Poznámka sa stane tiketom tlačidlom „Založiť tiket“, keď vyberieš druh práce. Poradca prácu na budúcu verziu navrhuje
ako tiket projektu.

**Aplikácia smie volať Clauda len cez predplatné.** Staviteľ aj Audítor majú toto pravidlo v charte. Kokpit
neschváli Špecifikáciu, ktorá od zákazníka žiada kľúč API Anthropic, a takú aplikáciu nenasadí. Platí štandard ICC:
predplatné Claude, nikdy kľúč API.
