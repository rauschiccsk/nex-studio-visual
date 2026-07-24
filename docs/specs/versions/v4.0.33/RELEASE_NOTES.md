# v4.0.33 — NEX Studio Visual

## Opravy pre prácu viacerých používateľov

Po zavedení kont pre viacerých používateľov sa ukázali nedostatky, ktoré táto verzia rieši:

- **Každý vidí len svoj kontext** — nový používateľ už v bočnom paneli, Metrikách, UAT ani PROD **nevidí cudzí projekt**. Predtým sa naposledy pripnutý projekt držal v prehliadači a „presakoval" medzi používateľmi; po novom sa pri každom prihlásení vynuluje, takže každý začína bez pripnutého projektu.
- **Aktualizácie fungujú pre všetkých** — karta Aktualizácie bola prázdna, lebo popisom chýbali podklady v serverovom balíku. Doplnené — changelog sa teraz zobrazuje každému rovnako.
- **Moje konto je editovateľné** — v „Moje konto" si po novom každý používateľ upraví **svoj e-mail, meno a Telegram ID** (nielen heslo). Rolu a aktívnosť konta naďalej spravuje len správca.

Bezpečnosť: úpravou vlastného profilu si používateľ nemôže zmeniť rolu ani sa aktivovať/deaktivovať — tie ostávajú výhradne v rukách správcu.
