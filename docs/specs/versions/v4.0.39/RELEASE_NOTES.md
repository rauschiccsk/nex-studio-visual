# v4.0.39 — NEX Studio Visual

## Oprava: šablóny pri zakladaní projektu

Zakladanie projektu pri príprave „pravidiel agenta" potrebuje sadu šablón (charter agenta, CI, smoke test…), ktoré chýbali v serverovom balíku — preto zakladanie padalo hneď po vytvorení pracovného priečinka. Šablóny sú teraz súčasťou balíka. Overené: založenie projektu prejde celým procesom (vytvorenie → pravidlá agenta → odoslanie na GitHub).
