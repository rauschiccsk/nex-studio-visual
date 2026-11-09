# v4.123.0

**Kokpit využíva celú plochu obrazovky.** Stránky už nemajú pevnú úzku šírku v strede (doteraz najviac 768 až 1 152 bodov
podľa stránky) — obsah každej stránky zaberá aspoň 90 % plochy vedľa ľavého menu, aj na širokej obrazovke. Meno zákazníka
na stránkach UAT a PROD sa už neláme do dvoch riadkov, kým je miesto. Stránka Aktualizácie je tiež široká (nex-shared
0.35.0). Kontrola v prehliadači to meria na každej stránke pri šírke 1 920 bodov a kontrola zdrojových súborov nepustí
stránku, ktorá by si znova nastavila úzku šírku.
