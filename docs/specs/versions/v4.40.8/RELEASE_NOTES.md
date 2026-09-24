# v4.40.8

Nasadenie na server zákazníka sa **zastaví**, keď sa nastavenie, ktoré drží kokpit, rozišlo s tým, čo na tom serveri naozaj beží. Doteraz by ho ticho prepísalo — a keby tam medzitým niekto niečo zmenil, zákazník by sa k aplikácii nemusel dostať.

Zastaví sa aj vtedy, keď sa stav na cieli **nedá prečítať**. Nasadzovať práve vo chvíli, keď o cieľovom serveri nič nevieme, je to najhoršie možné poradie.
