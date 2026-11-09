# v4.126.1

**Skúška nasadenia sa už neodlišuje od nasadenia v sieťach ani v povinných údajoch zákazníka.** Rýchla oprava Author
Asistenta 1.0.1 neprešla Verifikáciou pre chybu skúšky, nie aplikácie. Skúška odstraňovala všetky siete aplikácie, aj
vnútorné, cez ktoré Author Asistent zámerne púšťa pracovníka s umelou inteligenciou von len cez svoju bránu; teraz
odstráni len vonkajšie siete a vnútorné ponechá pod vlastným menom. Povinné údaje zákazníka, ktoré Manažér zadá pri prvej
inštalácii (napríklad prihlasovacie meno a inicializačné heslo autorky), skúška nikomu nedala zadať a aplikácia sa bez
nich nespustila; teraz dostanú skúšobné hodnoty tým istým pravidlom ako skúška spustenia. Skutočné údaje zákazníka sa do
skúšky nedostanú nikdy. Overené naživo: skúška Author Asistenta 1.0.1 prešla pre novú inštaláciu aj pre inštaláciu TATA.
