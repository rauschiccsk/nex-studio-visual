# v4.43.25

**Kontrola verzie (Verifikácia) už nezahltí linku servera.** Audítor pri každej kontrole sám sťahoval obraz na skúšky
v prehliadači (2,56 GB) a na konci ho zmazal — 11.10. päťkrát, zakaždým na asi 20 minút spomalil všetky stránky
zákazníkov. Teraz ten obraz pripraví kokpit a nechá ho na serveri: keď ho Audítor po sebe zmaže, kokpit ho pred ďalšou
kontrolou len znovu pomenuje, nič sa nesťahuje. Agent, ktorý vidí Docker servera, navyše nesmie obrazy sťahovať ani
hromadne čistiť; zákaz kokpit pridáva sám pri každom spustení, pre všetky projekty. Audítor môže aplikáciu naďalej sám
spustiť a overiť — kontrola ostáva rovnako dôkladná.
