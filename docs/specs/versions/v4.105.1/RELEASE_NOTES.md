# v4.105.1

**Nasadenie na iný stroj sa zastaví, keď v tom istom priečinku na ANDROSe stojí inštalácia ANDROSu.** Kokpit drží súbory
inštalácie na inom stroji aj u seba, v tej istej ceste. Keď v nej na ANDROSe beží alebo stojí inštalácia (napríklad stará
ostrá prevádzka zákazníka pred presunom na MAGER), nasadenie nič nezapíše a povie, ktoré kontajnery prekážajú a že ich treba
najprv zastaviť a odložiť. Rovnako sa zastaví, keď to kokpit nevie zistiť.
