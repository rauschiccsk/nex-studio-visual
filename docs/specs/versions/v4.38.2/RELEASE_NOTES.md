# v4.38.2 — brána nájde väzbu aj vtedy, keď vznikne neskôr

Predošlé vydanie zaviedlo kontrolu, že každá vymenovaná poistka má **svoju** odskúšanú skúšku.
Lenže tú väzbu nenachádzala — a tak sa potichu vracala k starému počítaniu. Odteraz ju nájde.

## Prečo

Zoznam poistiek vzniká pri uzavretí návrhu. Meno skúšky, ktorá poistku dokazuje, sa však vyberá až
neskôr — vtedy, keď tú skúšku niekto naozaj píše. Brána sa pozerala len na návrhový uzáver, takže
väzby doplnené neskôr nevidela.

Prišlo sa na to hneď pri prvom použití: stavba mala **všetkých štrnásť poistiek zviazaných** a brána
z nich videla **nula**. Prešla, ale len preto, že počet skúšok sedel — teda presne tak, ako predtým.

## Čo to znamená pre teba

Kontrola, ktorú si dostal v predošlom vydaní, odteraz naozaj funguje. A je v nej poistka aj opačným
smerom: neskoršie hlásenie smie k poistke väzbu **pridať**, ale nesmie poistku zo zoznamu odobrať —
stavba si tak nemôže zmenšiť vlastnú deklaráciu a ujsť pokrytiu, ktoré už sľúbila.
