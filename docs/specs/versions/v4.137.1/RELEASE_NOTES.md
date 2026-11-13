# v4.137.1

**Keď skúška spustenia po oprave zlyhá pri sťahovaní balíkov, kokpit to povie rovno a skúsi to ešte raz sám.**
Doteraz si z chybového výpisu stavby nechal len posledné riadky a dôvod sa stratil — nebolo vidieť, či sa balík
nepodarilo stiahnuť, alebo je chyba v aplikácii, a hláška vždy radila napísať agentovi, čo opraviť. Teraz kokpit
v technickom detaile ukáže riadky, ktoré nesú príčinu, a výpadok sťahovania (sieť, menný server, preťažený register)
spozná: skúšku hneď zopakuje a ďalej ide sám, keď druhý pokus prejde. Keď zlyhá aj ten, napíše, že balík sa
nepodarilo stiahnuť a nie je to chyba v kóde — stačí stlačiť „Skús znova“ bez písania. Staviteľ vtedy dostane
pokyn kód nemeniť a chybu nehľadať. Keď kontrola zlyhá pre chybu v aplikácii, postup ostáva ako doteraz.
