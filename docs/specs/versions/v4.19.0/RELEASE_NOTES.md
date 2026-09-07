# v4.19.0 — Nasadenie už zákazníkovi neposiela náradie, ktorým sa appka stavala

Pri každom nasadení do UAT skončil jeden kontajner chybou — naposledy **727 chýb**
naraz. Appka pritom bežala a bola zdravá.

## Čo to bolo

V zákazníkovom stacku sa spúšťal aj **kontajner s testami**. Existuje preto, aby
CI vedelo spustiť sadu proti skutočnej databáze; do zákazníka sa dostal len tak, že
nasadenie kopíruje všetky služby, ktoré v projekte nájde.

Tam ale žiadnu testovaciu databázu nemá — a tak zlyhal zakaždým.

## Prečo to nebola kozmetika

Nasadenie skončilo hlásením, ktoré vyzeralo presne ako rozbitá appka. Kto to vidí
pri každom nasadení, prestane to čítať — a v ten deň, keď je červená naozaj, si ju
nikto nevšimne. Presne to sa už raz stalo.

## Čo sa mení

Kontajner s testami sa u zákazníka **nespúšťa**. V súbore ostáva — vyrenderovaný
compose je naďalej verná kópia originálu a kto ho chce spustiť, môže — len sa
obyčajným štartom preskočí.

Rozpoznáva sa **podľa toho, čo si pýta**, nie podľa názvu: služba, ktorá žiada
testovaciu databázu, sa tým sama priznáva. Názov je vec autora appky.

## Poistky, aby to nikdy nevynechalo viac, než má

Nikdy sa nevynechá backend, frontend, databáza, migračná služba, ani nič, na čo
iná služba čaká. Appka bez kusu je horšia než hlučné nasadenie.
