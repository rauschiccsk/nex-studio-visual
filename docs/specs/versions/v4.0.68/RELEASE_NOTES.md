# v4.0.68 — NEX Studio Visual

## Písmo je ostré

Predchádzajúca verzia doniesla správne písmo, ale vykresľovalo sa zle — a to bola druhá polovica problému.

Aplikácia mala zapnuté nastavenie, ktoré znie ako vylepšenie („vyhladzovanie"), ale v skutočnosti **vypína jemné vykresľovanie, ktoré využíva subpixely obrazovky**, a nahrádza ho hrubším sivým. Na počítačoch Apple to vyzerá elegantne, na Linuxe a Windowse to písmo stenčí, rozmaže a zníži jeho kontrast.

Nastavenie je preč. Písmo sa teraz vykresľuje tak, ako to tvoj operačný systém robí najlepšie — ostrejšie a s väčším kontrastom, hlavne pri malých veľkostiach a pri našej diakritike.
