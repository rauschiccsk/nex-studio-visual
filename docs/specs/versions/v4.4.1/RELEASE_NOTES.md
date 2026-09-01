# v4.4.1 — verdikt sa číta ako veta a je len jeden

Dve chyby, obe na tej istej správe: na verdikte, ktorý Manažér číta pri
rozhodnutí, či verziu uzavrie. Je to najdôležitejšia veta celej stavby.

## Do textu presiakol strojový zápis

Na obrazovke stálo uprostred vety toto:

> …peniaze počíta na cent a vzhľad zostal taký, aký si schválil.`</summary>`
> `<parameter name="findings">["NEBLOKUJÚCE — čistička citlivých údajov…`

Audítor vložil svoje strojové značkovanie priamo do poľa určeného pre ľudský
text a systém uložil to, čo dostal. **Nič sa nestratilo** — nálezy sa
spracovali správne a nič nebolo nepravdivé. Len sa to nedalo čítať a pôsobilo
to ako porucha.

Text sa teraz zastaví pred prvou strojovou značkou. Všetko za ňou je aj tak
uložené inde, takže sa nič nezahadzuje — a veta bez značiek zostáva nedotknutá.

## Ten istý odstavec sa zobrazoval dvakrát

Systém zapisoval verdikt dvakrát: raz ako správu od Audítora a raz ako záznam
rozhodnutia. Obe išli Manažérovi, takže videl ten istý text dva razy pod sebou.

Pri úspešnom overení je to zbytočný šum. **Pri neúspešnom je to horšie** — dva
rovnaké červené odstavce sa čítajú ako dva rôzne problémy, práve keď sa treba
rozhodnúť, čo s jedným.

Teraz sa rozhodnutie **pripíše k správe, ktorá už na obrazovke je**, namiesto
pridania druhej. Nič sa tým nestráca: podrobná správa Audítora aj údaje
o rozhodnutí sú na jednej bubline.

Keď za verdiktom žiadna správa nie je — čo sa stáva, ak ho vyhlási sám systém
pri zlyhaní kontroly — zapíše sa ako doteraz. A opakované overenie nikdy
neprepíše výsledok toho predošlého; história zostáva.
