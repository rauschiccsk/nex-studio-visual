# v4.179.0

**Webový projekt na šablóne aplikácií.** Pri založení projektu môžete zvoliť Web aj so šablónou „NSV štandard“:
administrácia (prihlásenie menom) aj web (prihlásenie e-mailom) prídu zo šablóny. Web má vlastné kontá — e-mail je
medzi nimi jedinečný — a registrácia je žiadosť, ktorú schvaľuje správca v Nastaveniach → Kontá webu. Staviteľ stavia
len obrazovky webu, prihlásenie a schvaľovanie nestavia.

**Pridať webový frontend aj projektu na šablóne.** Účet admin môže pridať web aj projektu, ktorý už na šablóne je
(kým nebol nasadený do PROD a s dôvodom). Web príde zo šablóny a administrácia aj server projektu prejdú na jej
najnovšiu verziu, lebo web potrebuje jej server. Nič iné z projektu sa nemení.

**Oprava: webový projekt sa dá založiť.** Doteraz sa webový projekt dal len prepnúť z bežného — pri založení nedostal
port webu a databáza ho odmietla. Port webu sa teraz odvodí z bloku portov projektu, rovnako ako pri pridaní webu.
