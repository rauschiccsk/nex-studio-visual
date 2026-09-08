# v4.26.0 — vidno, koľké kolo konzultácie beží

## Čo chýbalo

Keď nezávislá previerka nájde v Špecifikácii alebo Návrhu medzeru, spustí sa konzultácia
s manažérom. Tá sa môže zopakovať najviac päťkrát; potom rozhoduje manažér sám.

Strop fungoval. Neviditeľné bolo **počítadlo**: kolá 1 až 4 ohlásili iba „spúšťa sa
konzultácia" a číslo sa objavilo až v okamihu eskalácie — teda keď už bolo po všetkom.

Na nex-productcatalogs v0.2.0 to znamenalo päť kôl za dva a pol hodiny a 23 rozhodnutí,
pri ktorých manažér nemal ako vedieť, či je na začiatku, alebo pred posledným kolom.

## Čo je odteraz inak

Nad rozhodovacími kartami stojí riadok **„Kolo konzultácie 2 z 5"**, a pri poslednom kole
sa dodáva, že ďalšie už nebude. Ohlásenie previerky hovorí to isté hneď na začiatku.

Číslo pochádza z toho istého počítadla ako strop, takže sa s ním nemôže rozísť — dve
nezávislé počítania toho istého by sa raz rozišli a manažér by čítal číslo, ktoré neplatí.

Kolá sa počítajú **na jeden spor**, nie na celú verziu. Bez toho by jeden vyčerpaný spor
ukázal „kolo 6 z 5" pri úplne inej otázke.

Staršie záznamy číslo kola nemajú; vtedy sa riadok jednoducho neukáže. Vymyslené číslo by
bolo horšie než žiadne.

## Stráž

Panel rozhodnutí bol dovtedy **bez jedinej stráže** — pritom je to obrazovka, pri ktorej
manažér sedí najdlhšie. Pribudli.

## Poznámka k pôvodnému zneniu

Tiket pôvodne tvrdil, že predbežná previerka strop vôbec nemá. Nebola to pravda — hľadal
som ho vnútri jednej funkcie, kde nie je, a zo záporného nálezu jedného hľadania som
usúdil, že nie je nikde.
