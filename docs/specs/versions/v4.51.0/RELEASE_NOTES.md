# v4.51.0

**Prázdny plán úloh povie, kedy plán vznikne.** Pri stavbe novej verzie po fázach (Príprava → Návrh → Vizuál →
Programovanie) vzniká plán úloh až na začiatku Programovania — zo schváleného Návrhu a zmien vo Vizuáli. Panel
„Plán úloh“ to teraz aj tak povie; doteraz písal, že sa plán objaví po dohode na špecifikácii, hoci tá bola dávno
schválená. Pri rozhovorovej stavbe a pri rýchlej oprave hovorí ich vlastnú vetu.

**Z tiketu v Plane rovno do Zásobníka alebo do rýchlej opravy.** V záložke „Plane tikety“ má každý otvorený tiket,
ktorý ešte nikam nešiel, dve tlačidlá. „Do Zásobníka“ z neho urobí požiadavku REQ-N prepojenú s tiketom — tiket sa
potom posúva sám ako tiket založený zo Zásobníka a do komentára sa zapíše, ako požiadavka vznikla. „Spustiť rýchlu
opravu“ otvorí zadanie zložené z názvu a textu tiketu s vopred zvoleným druhom práce; po úprave a kliknutí vznikne
opravná verzia ako doteraz. Tiket rýchlu opravu sleduje: pri spustení prejde do In Progress, pri nasadení na UAT
dostane komentár a po nasadení na PROD sa uzavrie (Done) s verziou, zákazníkom a dátumom. Jeden tiket ide jednou
cestou — tiket v Zásobníku rýchlu opravu nespustí a z tiketu s rýchlou opravou nevznikne požiadavka. Keď Plane
neodpovie, oprava aj požiadavka ostanú a kokpit povie vetou, čo sa do tiketu nezapísalo.

**Poradca navrhne tiket v Plane.** Keď Poradca v rozhovore nájde problém samotného kokpitu, navrhne tiket pre
Andros Deda; keď ide o prácu na projekte, navrhne tiket projektu s druhom práce a návrhom, či patrí do ďalšej verzie
alebo do rýchlej opravy. Pod odpoveďou sa objaví tlačidlo „Založiť tiket“. Otvorí náhľad, v ktorom môžeš upraviť
názov, popis a typ. Pri tikete projektu volíš aj druh práce, návrh a zodpovedného. Náhľad ukáže aj podobné otvorené
tikety, aby nevznikali dvojníky. Tiket kokpitu dostane sám aj projekt, verziu, stav stavby a odkaz na rozhovor.
Tiket vznikne až tvojím kliknutím a druhé kliknutie nezaloží ďalší. Tiket projektu je len návrh — verziu nezakladá
ani nespúšťa.

**Skúšky nových projektov už nezahlcujú internet v ANDROSe.** Šablóna skúšok (CI), ktorú kokpit dáva každému novému
projektu, si na našom bežci v ANDROSe sťahovala pri každom commite zásobu balíkov z GitHubu — pri NEX Manageri asi
1 GB — a novší commit nezastavil starší beh. Pri stavbe sa behy hromadili a 15.10.2026 na hodiny zaplnili linku
firmy. Bežec má balíky uložené na vlastnom disku, takže ich už nesťahuje, a novší commit zastaví starší beh tej istej
vetvy. Kokpit sa pýta na výsledok skúšok len pri najnovšom commite, takže zastavený starší beh nič neblokuje.
NEX Manager a NEX Websites dostali rovnakú úpravu priamo; NEX Inbox skúša na serveroch GitHubu a linku nezaťažuje.

**Jednorazové kroky inštalácie už po skončení nehlásia „unhealthy“.** Kroky, ktoré pri nasadení len prebehnú a
skončia (napríklad migrácia databázy), bežia z obrazu backendu a dedili jeho kontrolu zdravia. Keďže nič
neobsluhujú, po úspešnom skončení ostali označené ako choré a strážca by z toho hlásil falošný poplach. Kokpit im
teraz zdedenú kontrolu vypne; kontrolu, ktorú si krok určil sám, a kontroly bežiacich služieb nechá tak. Inštalácie
to dostanú pri najbližšom nasadení.
