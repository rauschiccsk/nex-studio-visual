# v4.103.3

**Nasadenie aplikácie na šablóne NSV štandard už nezastane pri stavbe obrazov.** Kokpit dáva stavbe aj ďalšie
priečinky projektu, ktoré backend šablóny potrebuje (frontend, docs), a pri stavbe z overeného commitu ich berie
z neho. Opraví to aj inštaláciu, ktorú zlyhané nasadenie už zapísalo.

**Skúšobná služba projektu sa už nedostane do inštalácie zákazníka.** Kokpit ju spozná aj podľa receptu obrazu
skúšok (`Dockerfile.test`), nielen podľa premennej testovacej databázy. V inštalácii, kde dosiaľ bežala s aplikáciou,
ju najbližšie nasadenie odstaví a povie to.
