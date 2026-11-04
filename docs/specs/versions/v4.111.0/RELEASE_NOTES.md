# v4.111.0

**Kokpit stráži miesto na disku servera, na ktorom stavia.** Pred nasadením zmeria voľné miesto na disku Dockera
cieľového stroja; keď nestačí na stavbu (asi 2,5-násobok obrazu aplikácie) a rezervu 10 GB pre databázy na tom istom
disku, nasadenie sa nezačne a kokpit povie, koľko miesta je voľného a koľko treba. Po úspešnom nasadení zmaže obrazy
starších verzií inštalácie — ponechá bežiacu a predošlú na návrat — a zmenší medzipamäť stavieb; na obrazovke nasadenia
napíše, čo uvoľnil. Nasadenie, ktoré zlyhalo pre plný disk, to povie vetou, nie hláškou Dockeru. Doteraz sa disk
servera MAGER pri stavbe NEX Knowledge zaplnil a databáza inej aplikácie na ňom sa musela reštartovať.
