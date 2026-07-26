# v4.0.56 — NEX Studio Visual

## Riadiace centrum už neprotirečí samo sebe

Keď sa v projekte po označení verzie za hotovú zmenil kód, Riadiace centrum ukazovalo naraz tri veci, ktoré si odporovali: hore zelené **„Hotovo — pripravené na nasadenie"**, vpravo v Pláne úloh vetu **„Verzia je hotová a pripravená na nasadenie k zákazníkovi"** s tlačidlom **„Prejsť na nasadenie"** — a dole oranžové upozornenie, že overenie je zastarané. Manažér nemal ako vedieť, čomu veriť, a jediné tlačidlo z tej trojice viedlo na obrazovku, ktorá verziu aj tak odmietla nasadiť.

Odteraz v takom stave:

- **Stavový pruh** hore zmení farbu na oranžovú a povie **„Hotovo — kód sa odvtedy zmenil, treba znova overiť"**.
- **Karta v Pláne úloh** už netvrdí, že je verzia pripravená. Namiesto toho vysvetlí, že sa kód po označení za hotové zmenil, a nasmeruje na tlačidlo **„Over znova"**, ktoré je na tej istej obrazovke.
- **Tlačidlo „Prejsť na nasadenie"** ostáva viditeľné, ale je zošednuté — po najazdení kurzorom povie prečo. Nikto sa už neprepracuje na obrazovku nasadenia len preto, aby tam zistil, že sa nedá pokračovať.

Rozlišujeme pritom dve odlišné situácie, ktoré sa doteraz zlievali: **„overenie sa nedá potvrdiť"** (nevieme to posúdiť) a **„kód sa odvtedy zmenil"** (vieme, že overenie už neplatí). Každá má iné riešenie, tak o každej hovoríme inak.
