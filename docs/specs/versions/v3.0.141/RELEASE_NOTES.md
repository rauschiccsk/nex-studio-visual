## v3.0.141 — Overenie: koniec nekonečnej slučky pri opravách

Keď overenie našlo chybu a spustila sa oprava, systém omylom **pridával každý opravný cyklus do zoznamu zmien (Aktualizácie)** generovanej appky. Zoznam sa tak pri každom kole rozišiel, overenie znovu padlo — a točilo sa to dokola. Interné opravné cykly už do zoznamu zmien **nepatria**, takže sa appka po oprave overí bez tejto slučky.
