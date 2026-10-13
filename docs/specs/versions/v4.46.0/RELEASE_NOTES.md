# v4.46.0

**Keď kokpit zapíše poznámky k vydaniu, pregeneruje aj súbor, ktorý si z nich robí aplikácia.** NEX Inbox si
z poznámok skladá vlastný zoznam noviniek a jeho kontrola na GitHube stráži, aby s nimi sedel. Kokpit doteraz zapísal
len poznámky, takže kontrola zlyhala a stavba dostala zbytočné kolo opráv. Teraz kokpit spustí generátor, ktorý
projekt sám ohlási, a zapíše oboje naraz. Keď generátor zlyhá, poznámky do gitu nezapíše a povie Manažérovi prečo.

**Nasadenie projektu len pre súkromnú sieť už nehlási falošné zlyhanie.** Po nasadení kokpit overuje, či je
aplikácia dostupná na svojej adrese. Pri projekte, ktorý je dostupný len zo súkromnej siete, napríklad Career
Asistent, však skúšal verejnú adresu, ktorá zámerne neexistuje, a nasadenie označil za zlyhané, hoci aplikácia
bežala. Teraz overuje tú adresu, na ktorej projekt naozaj beží.

**Zámerne červený dôkaz agenta už v Repozitári nevyzerá ako chyba.** Keď agent dokazuje, že nová kontrola chybu
naozaj chytí, schválne ju pokazí na bočnej vetve a kontrola zčervenie. Repozitár to doteraz ukázal ako „Zlyhalo“,
rovnako ako skutočnú chybu. Teraz taký beh ukáže ako „Dôkaz — červené podľa plánu“ aj s krokom, ktorý zčervenel.
Ak by dôkaz nečakane prešiel, ukáže „Dôkaz prešiel — mal byť červený“, lebo to znamená, že kontrola nič nechytila.
Agenti dostali pravidlo, ako taký dôkaz označiť.
