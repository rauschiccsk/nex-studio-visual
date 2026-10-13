# v4.46.0

**Keď kokpit zapíše poznámky k vydaniu, pregeneruje aj súbor, ktorý si z nich robí aplikácia.** NEX Inbox si
z poznámok skladá vlastný zoznam noviniek a jeho kontrola na GitHube stráži, aby s nimi sedel. Kokpit doteraz zapísal
len poznámky, takže kontrola zlyhala a stavba dostala zbytočné kolo opráv. Teraz kokpit spustí generátor, ktorý
projekt sám ohlási, a zapíše oboje naraz. Keď generátor zlyhá, poznámky do gitu nezapíše a povie Manažérovi prečo.
