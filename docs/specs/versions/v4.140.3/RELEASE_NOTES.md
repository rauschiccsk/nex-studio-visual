# v4.140.3

**Aj chyby z vnútra kokpitu prídu po slovensky.** Pri práci so zákazníkmi, verziami, používateľmi, prístupmi,
nastaveniami, Znalostnou bázou či nasadením kokpit doteraz pri chybe ukázal anglický text — napríklad „Customer with
slug … already exists in this project“ alebo „Current password is incorrect“. Teraz povie po slovensky, čo sa stalo:
„Zákazník so slugom … už v tomto projekte je.“, „Súčasné heslo nie je správne.“ Kokpit už nerozlišuje druh chyby
(neexistuje / už existuje / nedá sa) podľa anglických slov v texte, ale podľa druhu chyby, takže sa správa rovnako
ako doteraz. Nová kontrola stráži každú chybu, ktorú môže kokpit ukázať človeku.
