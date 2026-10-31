# v4.105.3

**Na MAGERi ukladá nová inštalácia súbory aplikácie do svojho priečinka, ktorý zálohuje nočná záloha.** Súbory mimo
databázy (napríklad prijaté e-maily v Career Asistentovi či nahraté súbory v NEX Knowledge) dostane nová inštalácia na
MAGERi do priečinka `/opt/customers/<zákazník>/<projekt>/` namiesto skrytého úložiska Dockera, ktoré záloha MAGERa
nevidí. Databázy ostávajú tam, kde boli — zálohuje ich výpis. Na ANDROSe a v stojacich inštaláciách sa nemení nič.
