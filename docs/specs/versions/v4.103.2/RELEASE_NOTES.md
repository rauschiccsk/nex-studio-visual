# v4.103.2

**Prevzatie ručnej inštalácie už nezastane na kroku, ktorý beží na obraze inej služby.** Keď inštalácia spúšťa
napríklad migráciu na obraze backendu, nasadenie novej verzie ju postaví rovnako ako backend. Náhľad prevzatia sa
teraz pýta to isté ako nasadenie: obraz, ktorý by nikto nepostavil ani nenašiel, ohlási vopred a prevzatie nepustí.
