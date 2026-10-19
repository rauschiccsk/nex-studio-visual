# v4.53.3

**Opravená kontrola už nebráni nasadeniu.** Keď kontrola (CI) jedného postupu zlyhala a neskoršia kontrola toho
istého postupu na tom istom kóde prešla, kokpit si pamätal zlyhanie a nasadenie blokoval. Teraz platí posledný
výsledok každého postupu a pri zelenej kokpit povie, ktorú staršiu chybu novšia kontrola prekonala. Zrušená kontrola
starú chybu neprekoná — o kóde nič nehovorí.
