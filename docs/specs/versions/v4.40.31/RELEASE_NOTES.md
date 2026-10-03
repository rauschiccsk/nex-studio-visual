# v4.40.31

Dokončenie opravy runnerov z 4.40.30. Runner sa už smel aktualizovať sám, no v kontajneri sa jeho
aktualizácia mohla zraziť s reštartom kontajnera a runner potom ostal nefunkčný. Teraz prebehne
aktualizácia vnútri bežiaceho runnera, rovnako ako pri runneroch priamo na serveri, a kontajner sa pri nej
vôbec nereštartuje.
