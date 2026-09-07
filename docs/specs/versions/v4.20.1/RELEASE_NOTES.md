# v4.20.1 — dorobenie ochrany Zadania

Ochrana z v4.20.0 funguje — a pri prvom skutočnom použití ukázala dve vlastné chyby.
Obe sú opravené.

## Formulár sa po odmietnutí zasekol

Verzia vzniká **skôr**, než sa uloží Zadanie. Keď sme zápis odmietli, verzia už
existovala — a druhý pokus padol na „takú verziu už máš". Nedalo sa pokračovať ani
ustúpiť.

Formulár si teraz založenú verziu pamätá. Druhý pokus ju použije namiesto toho, aby
ju zakladal znova.

## Dve čísla o tej istej veci

Hláška hovorila „71 riadkov", panel pod ňou „72 riadkov". To isté číslo sa počítalo
na dvoch miestach a jedno z nich rátalo inak.

Panel už žiadne číslo neuvádza — počet povie hláška, panel ukazuje samotný text.
