# v4.139.6

**Kokpit ponúkne povýšenie šablóny aj NEX Manageru.** NEX Manager má server šablóny aplikácií zapísaný inak než
projekty, ktoré kokpit založil sám (ako závislosť nástroja poetry), a kokpit ho preto nevedel prečítať — povýšenie
šablóny mu neponúkla stránka „Nová verzia“ ani pruh so zadaním od nášho technického tímu. Teraz kokpit prečíta oba
zápisy a pri povýšení zapíše novú verziu v tom zápise, ktorý projekt má, prepočíta jeho zámok závislostí a overí, že
naozaj ukazuje na novú verziu — inak všetko vráti.
