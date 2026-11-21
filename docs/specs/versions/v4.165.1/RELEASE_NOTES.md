# v4.165.1

**Disk sa po CI čistí naozaj každý deň.** Kokpit každý deň čistí disky bežcov CI od starých obrazov a medzipamäte.
Doteraz čakal celý deň od svojho štartu a každé nasadenie kokpitu ten deň začalo odznova, takže sa k čisteniu nedostal.
Teraz si pamätá, kedy čistil naposledy, a po štarte čaká len zvyšok dňa. Keď je čistenie na rade, prebehne pár minút
po štarte.
