# v4.0.51 — NEX Studio Visual

## „Preč" pošle upozornenie aj keď projekt sleduje niekto iný

Keď si používateľ zapol **„Preč"**, no ten istý projekt mal otvorený **niekto iný** (napr. správca „pri počítači"), Telegram upozornenie sa **potlačilo** — a tak neprišlo ani tomu, kto je preč.

Opravené:

- Kto je **„Preč"**, dostane upozornenie na Telegram **bez ohľadu** na to, či projekt sleduje niekto iný. Potláča sa už len prípad, keď **nikto nie je preč** a niekto aktívne sleduje (ten to uvidí priamo v cockpite).

Takže keď Junior odíde od počítača a jeho projekt narazí na rozhodnutie, upozornenie mu príde, aj keď má správca cockpit otvorený.

*(Podmienkou je platné Telegram chat ID — jeho nastavenie a overenie zjednodušíme v ďalšej verzii.)*
