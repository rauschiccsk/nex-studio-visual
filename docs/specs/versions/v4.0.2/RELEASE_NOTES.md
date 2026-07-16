# v4.0.2 — NEX Studio Visual

## Nový projekt dostane svojho CI robota automaticky

Keď NEX Studio Visual založí nový projekt, nastaví mu aj **automatické kontroly kódu** (CI). Doteraz k nim ale chýbal „robot", ktorý ich reálne spúšťa — kontroly tak po každej zmene ostávali visieť a nikdy nedobehli, kým ho niekto nedoregistroval ručne.

Od tejto verzie si projekt tohto **CI robota vytvorí sám** hneď pri založení — kontroly (formát, testy, zostavenie) bežia od prvej zmeny bez akéhokoľvek ručného zásahu. Pri zmazaní projektu sa robot zase automaticky odstráni.
