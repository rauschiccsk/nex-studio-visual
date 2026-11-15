# v4.146.0

**„Otvoriť“ na stránke Inštalácie otvorí aj aplikáciu, ktorú spúšťa NEX Manager.** Takú aplikáciu (napríklad NEX Inbox,
NEX Knowledge, NEX Websites) tlačidlo doteraz otváralo len holou adresou a aplikácia bez lístka z NEX Managera nikoho
nepustila. Teraz ju „Otvoriť“ v UAT spustí rovno prihlásenú tvojím menom, rovnako ako „Spustiť“ na stránke UAT. V PROD
je namiesto neho „Otvoriť v NEX Manageri“ — ostrú aplikáciu otvoríš z NEX Managera zákazníka, ktorý rozhoduje, kto ju
smie otvoriť; keď zákazník NEX Manager v PROD ešte nemá, stránka to povie. Aplikácia s prihlásením heslom sa otvára
ako doteraz.
