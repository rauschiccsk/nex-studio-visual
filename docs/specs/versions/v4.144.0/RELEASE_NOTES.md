# v4.144.0

**Návod na skúšku v UAT ukáže celú adresu zákazníka.** Návod doteraz písal napríklad
„https://<adresa-inštalácie>/api/v1/health“ a adresu si Manažér musel poskladať sám, hoci ju kokpit pozná. Teraz panel
„Čo otestovať vo verzii“ dosadí na to miesto celú adresu UAT zákazníka, u ktorého skúšaš, ako odkaz, ktorý sa otvorí
kliknutím — v projekte s administráciou a webom zvlášť adresu administrácie a adresu webu. Staviteľ dostal pravidlo,
že adresu aplikácie nepíše natvrdo, ale zástupným miestom, takže jeden návod platí pre každého zákazníka.

**Kokpit všade hovorí „Manažér“.** Pravidlá agentov a jedna veta na obrazovke Nápadov nazývali Manažéra anglickým
slovom pre riaditeľa; agenti ho preberali do odpovedí a ľudia si mysleli, že ide o riaditeľa spoločnosti. Teraz pravidlá Poradcu, Staviteľa aj
spoločné pravidlá agentov, texty kokpitu a poznámky k vydaniu hovoria „Manažér“ a skúška stráži, aby sa slovo
nevrátilo.
