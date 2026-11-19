# v4.158.2

**Disk servera sa už nezaplní medzipamäťou kontrol.** Vykonávače kontrol (CI) si pri každej kontrole odkladali
pomocné súbory zo stavby aplikácií a nikto ich nemazal — disk ANDROSu sa tak naplnil na 85 %. Kokpit teraz raz denne
zmaže vo vykonávačoch to, čo sa nepoužilo dlhšie ako 3 dni. Vykonávač, ktorý práve robí kontrolu, nechá na pokoji,
a čerstvé súbory ponechá, aby kontroly ostali rýchle.
