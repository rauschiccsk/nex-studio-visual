# v4.39.0 — kokpit prestal zamlčiavať to, na čom záleží

Desať opráv s jedným spoločným menovateľom: **stav, ktorý sa nedal zbadať**. Kokpit o ňom vedel,
mal ho zapísaný — a ukazoval ho tam, kam sa nikto nepozerá, alebo vôbec.

## Prečo

Verzia NEX Inboxu prešla schválením do Hotovo s padajúcim zostavením. Nikto to neprehliadol z
nepozornosti: kokpit sa GitHubu na stav pýtal, ale z dvoch kontrol si vypýtal jednu a spracoval tú,
ktorá prišla prvá. Raz to bola tá zelená, raz tá červená — hod mincou.

To istý deň pokračovalo. Pozastavená stavba dala o sebe vedieť len zmeneným textom na tlačidle.
Panel hlásil verziu o štyri vydania starú. Tlačidlo na prevzatie inštalácie by prešlo a appka by
ticho prestala vidieť faktúry.

## Čo to znamená pre teba

**Verzia sa nedostane do Hotovo s červeným zostavením.** Brána číta všetky kontroly, nie jednu, a
v hlásení menuje, ktorá zlyhala.

**Prevzatie inštalácie ti vopred ukáže, čo sa prenesie a čo by sa stratilo** — pripojenia priečinkov,
pridelené podsiete, poštových hostiteľov. Keď by sa niečo stratilo, tlačidlo sa neponúkne.

**Pozastavená stavba to povie pruhom cez celú šírku** a rozlíši, či čaká na teba, či prekročila strop,
alebo či si ju pozastavil sám.

**Nad rozhodovacími kartami vidíš smer, nie stav** — koľko sa už uzavrelo a ako v jednotlivých kolách
klesali (alebo stúpali) blokujúce nálezy. Pri každej karte aj to, **prečo tá otázka vôbec vznikla**:
či je to dôsledok tvojho rozhodnutia, nález, čo tam ležal od začiatku, alebo vedome odložená vec.

**Karta po zlyhaní zostavenia povie príčinu rečou** a ponúkne opakované overenie namiesto kola opráv,
v ktorom niet čo opravovať.

**Panel prestal vydávať verziu obrazoviek za verziu celej appky.** Keď sa polovice líšia, uvidíš obe.

**Povinné celoICC štandardy vstupujú do stavby zadaním**, nie padnutou bránou na konci — takže sa
dostanú do Špecifikácie aj do Vizuálu ako každá iná obrazovka a neschvaľuješ ich dodatočne.

**Texty o prevzatí projektu prestali klamať.** Charta sa pri prevzatí prepisuje a pôvodná sa odkladá
bokom; dialóg to odteraz povie skôr, než potvrdíš.
