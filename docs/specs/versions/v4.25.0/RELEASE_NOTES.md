# v4.25.0 — kokpit už počas práce nemlčí

## Čo sa dialo

Po kliknutí na „Schváliť vizuál“ bežal ťah agenta aj tri minúty. V evidencii pritom
stále stálo „čaká na súhlas“ — takže obrazovka nezobrazovala nič. Director to opísal
slovami *„nič sa nedeje, nefunguje to“*. Agent celý ten čas pracoval.

## Prečo je ticho horšie než chyba

Ticho pri práci a ticho pri poruche vyzerajú rovnako. Manažér nemá ako rozhodnúť, či
počkať, kliknúť znova, alebo volať pomoc — a klikanie znova spúšťa ďalšie ťahy, čo
stojí beh agenta a mätie protokol.

## Čo je odteraz inak

Dokončenie schválenia — zloženie dohodnutého späť do dokumentov a Auditorova previerka
toho, čo pribudlo — už nebeží vnútri kliknutia. Kliknutie iba zapíše, že sa schválenie
spracúva, a hneď sa vráti; prácu urobí ten istý mechanizmus na pozadí, ktorý vykonáva
každý iný ťah.

Fáza sa posunie až tam, a len ak niet rozporu. Rozpor nie je porucha, ale otázka na
manažéra: schválenie vtedy zámerne neprejde, lebo usadiť ho ticho ktorýmkoľvek smerom
by ten nesúhlas pochovalo.

## Na obrazovke

Pruh stavu ukazuje počas práce dve veci, ktoré tam dovtedy neboli:

- **čo sa práve robí** — dovtedy sa veta z engine-u zobrazovala len počas čakania na
  súhlas, čiže presne vtedy, keď sa nič nedialo;
- **odkedy sa pracuje** — „pracuje sa 3 min“, „pracuje sa 2 h 5 min“. Bez toho sa ťah
  spustený pred pol minútou nedá odlíšiť od ťahu, ktorý visí tretiu hodinu.

Keď stavba nepracuje, čas sa neukazuje — inak by strašil na usadenej stavbe.

## Stráž

Nič nebráni tomu, aby niekto tú minútovú prácu vrátil späť do kliknutia — vyzerá to
tam prirodzene, veď schválenie ju spúšťa. Stráž preto priamo kontroluje, že vetva
schválenia tie kroky nevolá a že ich volá ťah na pozadí.
