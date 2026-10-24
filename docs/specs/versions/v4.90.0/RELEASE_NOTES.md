# v4.90.0

**Každý projekt má na ANDROSe dva vykonávače kontrol (bežce CI) namiesto jedného.** Agent po každej úlohe odošle
zmenu na GitHub a kontroly sa pri stavbe zbiehajú; s jediným vykonávačom na seba čakali — pri NEX Inboxe až 34 minút.
Teraz si úlohy projektu berie ten vykonávač, ktorý je voľný. Každý má vlastný Docker aj vlastný pracovný priečinok,
takže si navzájom nič nezmažú. Počet je v nastavení kokpitu. Záložka Repozitár povie, na koľkých vykonávačoch
kontroly bežia; existujúce projekty druhý vykonávač dostanú pri obnove („Obnoviť vykonávač kontrol“).
