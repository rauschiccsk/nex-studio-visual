# v4.101.7

**Pravidlo „hlavná vetva po úlohe ostáva zelená“ žiada od agenta len to, čo pri úlohe naozaj vie overiť.** Skúšky
a lint spustí sám. Obraz na skúšky a štart aplikácie v Dockeri overia kontroly na GitHube a Verifikácia. Agent sa tak
nezasekne a nebude hlásiť overenie, ktoré nespravil.
