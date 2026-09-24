# v4.40.7

Keď ostrá prevádzka zákazníka beží na jeho vlastnom serveri, kokpit si tam pripraví priečinky pre jeho dáta sám — pred tým, než aplikáciu spustí. Bez toho by sa aplikácia rozbehla, ale nemala by kam ukladať prijaté dokumenty.

Robí to cez Docker, nie prihlásením sa na ten server: prístup, ktorý má kokpit na cudzí stroj, je zámerne obmedzený tak, že sa naň nedá prihlásiť.
