# v4.95.8

**Kontroly projektu na GitHube sa dajú spustiť znova priamo z kokpitu.** Keď Audítor verziu uznal a zastavili ju
len kontroly na GitHube (napríklad pri zostavení zlyhalo stiahnutie knižnice), karta Verifikácie ponúkne
„Spustiť kontroly znova“. Kokpit spustí znova len časti, ktoré zlyhali, a počká na ich výsledok. Keď prejdú, platí
verdikt Audítora pre ten istý commit a verzia ide rovno na schválenie, bez nového kola Audítora. Keď zlyhajú znova,
karta sa vráti. Doteraz karta odporúčala „Znova over bez opravy“, teda celé nové kolo Audítora, ktoré skončilo na tom
istom červenom behu, a kontroly bolo treba spustiť ručne na GitHube (Author Asistent 0.1.0). Voľbu ponúkne aj karta
stropu kôl.
