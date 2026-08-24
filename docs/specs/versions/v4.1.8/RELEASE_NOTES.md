# v4.1.8 — hlásenie o izolácii si počet dopočíta samo

Drobná oprava, ktorá sa našla hneď pri nasadení predošlej verzie.

Riadok, ktorý pri štarte oznamuje, ktoré fázy stavby bežia zatvorené vo vlastnom
priestore, ich všetky vymenoval — a v tej istej vete tvrdil, že sú **tri z
piatich**. Boli štyri. To číslo tam bolo napísané rukou vedľa zoznamu, ktorý sa
generuje, takže prvá zmena ho nechala pozadu.

Je to zdanlivo maličkosť, ale práve tento riadok je jediné miesto, z ktorého sa
dá zistiť, čo je a čo nie je izolované. Číslo si preto odteraz **dopočíta zo
zoznamu**, a strážny test kontroluje, čo sa naozaj vypíše — nie čo je napísané
v zdrojáku.
