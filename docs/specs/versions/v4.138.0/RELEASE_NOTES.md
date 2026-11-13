# v4.138.0

**Každá aplikácia povie, pre ktorého zákazníka beží a či je to skúšobná inštalácia.** Doteraz sa okno, ikona aj
hlavička každej inštalácie volali rovnako — napríklad „NEX Manager“ pre ICC aj pre MÁGERSTAV, na UAT aj na PROD.
Kokpit teraz pri každom nasadení povie inštalácii meno zákazníka a prostredie. Aplikácie na šablóne (nex-app-kit od
v0.13.0) to ukážu: názov okna „NEX Manager — MÁGERSTAV s.r.o. (UAT)“, meno zákazníka v ľavom paneli aj na karte
prihlásenia a skúšobná inštalácia má nad každou stránkou výrazný pás „Skúšobné prostredie (UAT)“. Rovnako sa pomenuje
aj nainštalovaná aplikácia na ploche a na lište. Aplikácia to dostane pri svojej ďalšej verzii na novej šablóne;
premenovaný zákazník sa premenuje pri najbližšom nasadení.
