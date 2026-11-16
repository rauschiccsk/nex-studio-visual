# v4.148.1

**Povýšenie šablóny aplikácie či knižnice obrazoviek sa uloží do gitu projektu aj v projekte, v ktorom sa ešte nič
nestavalo.** Doteraz takýto zápis odmietla kontrola projektu, ktorá pred každým zápisom kontroluje frontend — a ten
v nepostavenom projekte ešte nemá nainštalované balíky. Súbory sa zmenili, ale v gite ostali neuložené a Nastavenia
projektu to nepovedali. Kokpit teraz zapíše len súbory povýšenia, bez tejto kontroly; zmenu overí kontrola projektu
na GitHube ako každú inú.

Keď v projekte ostalo povýšenie šablóny, ktoré v gite chýba, Nastavenia projektu to pri úprave povedia a ponúknu
tlačidlo „Uložiť do gitu“.
