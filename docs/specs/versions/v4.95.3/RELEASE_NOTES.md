# v4.95.3

**Skúška po spustení pripraví aplikácii nastavenia tak ako UAT.** Keď projekt v `.env.example` necháva tajomstvá
prázdne, lebo ich má vyrobiť kokpit, skúška po spustení im teraz vyrobí náhodné hodnoty tým istým pravidlom ako
nasadenie na UAT. Adresu označenú `@public-url` doplní adresou skúšobného prostredia. Hodnoty, ktoré zadáva
zákazník, a premenné bez dohody ostávajú prázdne ako doteraz. Doteraz skúška tieto hodnoty len vynechala a aplikácia,
ktorá ich pri štarte vyžaduje (Author Asistent 0.1.0), v skúške nenaštartovala, hoci na UAT by naštartovala.
