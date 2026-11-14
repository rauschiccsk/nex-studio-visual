# v4.140.2

**Chybové hlášky kokpitu sú po slovensky.** Kokpit doteraz pri niektorých chybách odpovedal po anglicky — napríklad
pri prihlásení „Invalid username or password“, pri zakladaní projektu („Failed to create GitHub repository …“) alebo
pri odmietnutej akcii stavby („Pipeline already started for this version“). Teraz povie po slovensky, čo sa stalo a čo
urobiť: „Nesprávne meno alebo heslo.“, „Repozitár na GitHube sa nepodarilo založiť …“, „Stavba tejto verzie už bola
spustená.“ Kokpit už nerozlišuje druh chyby podľa anglických slov v texte, takže sa správa rovnako ako doteraz.
Nová kontrola stráži, aby sa do týchto hlášok anglický text nevrátil.
