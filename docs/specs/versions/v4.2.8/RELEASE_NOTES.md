# v4.2.8 — kontrola už neobviní nikoho a povie, čo naozaj namerala

## „Agent zlyhal" nad agentom, ktorý neurobil nič zlé

Hodinu po predošlom vydaní hlásila obrazovka stavby **„Niečo zlyhalo — Agent
zlyhal"**. Agent pritom svoju opravu spravil dobre: aplikácia sa spúšťala,
databáza aj migrácie boli v poriadku.

Zlyhalo niečo úplne iné — a kontrola to nevedela povedať, tak siahla po
najbližšom hotovom hlásení. To hlásenie však vždy niekoho obviňuje.

Odteraz má výsledok kontroly **vlastné hlásenie: „Kontrola neprešla"**. Nikoho
neobviňuje, lebo nikto nezlyhal — práca jednoducho ešte nie je hotová. Nie je
červené a neponúka „skús znova" naslepo; ponúka napísať, čo opraviť, a keď
nenapíšeš nič, agent dostane pokyn zistiť príčinu z výpisu posledného behu.

## Kontrola sa už nepýta nesprávnej veci

Skúška po spustení posudzovala výsledok podľa toho, či príkaz na spustenie
skončil bez chyby. Lenže ten príkaz **ohlási chybu aj vtedy, keď všetko dobehne
správne** — stačí, že sa dokončí jednorazová služba, napríklad príprava
databázy. Aj keď skončí úspešne.

Každý projekt, ktorý NEX Studio postaví, takú jednorazovú službu má. Kontrola
teda hlásila neúspech aj nad úplne zdravou aplikáciou. Doteraz to nebolo vidieť
len preto, že sa to trafilo do dní, keď boli chyby aj skutočné.

Teraz sa **pýtame kontajnerov, nie príkazu**:

- skončil niektorý kontajner chybou → povieme **ktorý** a priložíme jeho výpis
- neskončil žiadny chybou → spýtame sa samotnej aplikácie, či odpovedá

## Hlásenie tvrdí len to, čo sa zmeralo

Predošlá veta znela „appka sa stále nespustí" bez ohľadu na to, čo sa naozaj
stalo. Prvý raz, keď zaznela, bola nepravdivá.

Nové hlásenie povie jedno z troch — podľa toho, čo sa zistilo: *zlyhal kontajner
X*, *aplikácia sa nespustila*, alebo *spustenie neprešlo* (keď sa nedalo zistiť
nič bližšie). Keď zlyhal kontajner, hlásenie **netvrdí nič o tom, či aplikácia
nabehla** — nikto sa jej totiž v tej chvíli nepýtal.
