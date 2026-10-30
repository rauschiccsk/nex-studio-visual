# v4.103.4

**Kontrola vstupu po nasadení už neobviní aplikáciu, ktorú nevidela.** Kokpit skúša vstupenku tam, kam ju pošle
internet — cez Traefik s menom inštalácie —, takže prejde aj k aplikácii „len pre súkromnú sieť“. Najprv sa spýta
NEX Managera zákazníka na toho, kto nasadzuje: keď ho nepozná, má ho deaktivovaného alebo mu aplikáciu nepridelil,
nasadenie to povie presne a vstup nechá na vyskúšanie z NEX Managera.

**Nasadenie, ktoré odstaví skúšobnú službu, ju už nemenuje aj medzi jednorazovými krokmi.**
