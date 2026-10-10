# v4.43.15

**Pravidlá, ktoré dostáva agent stavby, sú kratšie a strážené.** AI Agent číta svoje pravidlá pri každej
práci a s dĺžkou ich dodržiava horšie. Kokpit teraz stráži, aby text, ktorý agent pri každej práci dostane,
nepresiahol 37 000 znakov — keď prerastie, skracuje sa text, nie hranica. Z pravidiel vypadli poznámky pre
vývojárov, zmienky o zrušených rolách, odkazy na neexistujúce miesta a opakovania; späť sa vrátila veta,
ktorá agentovi hovorí, že ku každej kľúčovej funkcii a ku každému bezpečnostnému pravidlu musí napísať
akceptačnú skúšku — od 22. júla z pravidiel omylom vypadla. Z kokpitu zmizli aj staré pravidlá a nástroje
z prvej verzie NEX Studia, ktoré už nič nepoužívalo.

**Agent stavby dostáva zručnosti.** Zručnosť je postup, ktorý agent potrebuje len niekedy — napríklad ako
opraviť zlyhanie z Verifikácie, ako spúšťať skúšky backendu alebo ako napojiť aplikáciu na spúšťanie z NEX
Managera. Agent pri práci vidí len ich krátky opis a celý postup si otvorí, keď ho potrebuje; charta agenta, ktorú
číta pri každej práci, je preto asi o pätinu kratšia. Kokpit zapisuje zručnosti do projektu spolu s pravidlami
agenta. Dve zručnosti, ktoré projekty dostávali doteraz, boli uložené v tvare, ktorý agent nevedel načítať —
nemal ich teda nikdy; odteraz ich má.

**Nové pravidlá sa k agentovi dostanú hneď, aj v prevzatých projektoch.** Kokpit doteraz obnovoval pravidlá
agenta v projekte len pri štarte novej verzie a v prevzatých projektoch (NEX Inbox, NEX Manager) vôbec — preto
agenti pracovali s pravidlami z 2. septembra a nič z neskorších vylepšení ich nedosiahlo. Odteraz kokpit
pravidlá aj zručnosti obnoví tesne pred každým novým sedením agenta, vo všetkých projektoch. Rozhovor, ktorý
agent práve vedie, sa nemení; nové pravidlá platia od jeho najbližšieho nového sedenia.
Platí to aj vtedy, keď sa sedenie agenta stratí a kokpit mu založí náhradné — doteraz náhradné sedenie
bežalo úplne bez pravidiel agenta.

**Verzia s červenými kontrolami sa už nedá nasadiť.** Pri dokončení verzie kokpit označí kód značkou verzie a tá
na GitHube spustí ďalšie kontroly projektu. Kokpit doteraz vyhlásil „CI zelené“ skôr, než sa tieto kontroly
vôbec rozbehli, a keď neskôr zlyhali, ponúkal verziu ďalej ako „pripravenú na nasadenie“. Odteraz na ne počká,
a keď kontroly na kóde, ktorý by sa nasadil, zlyhajú alebo ešte bežia, obrazovka nasadenia povie, ktorá kontrola
to je, dá odkaz na ňu a „Nasadiť“ ostane zavreté. Riadiace centrum v takom prípade nepíše „pripravené na nasadenie“.
