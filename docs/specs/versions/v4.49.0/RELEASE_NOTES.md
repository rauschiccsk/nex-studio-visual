# v4.49.0

**Pred nasadením na PROD kokpit zálohuje databázu.** Doteraz sa nová verzia dostala na ostrú inštaláciu zákazníka aj
s migráciou databázy bez zálohy — keby sa migrácia pokazila, nebolo by kam sa vrátiť. Teraz kokpit po postavení
obrazov, kým ešte beží stará verzia, vypíše databázu do priečinka inštalácie na tom istom stroji, overí, že sa výpis
dá prečítať, a až potom vymení kontajnery. Keď sa záloha nepodarí alebo na disku nie je miesto, nasadenie sa nezačne
a stará verzia beží ďalej. Priebeh nasadenia ukáže krok „zálohujem databázu“ a hlásenie povie, kde záloha leží;
ponechá sa posledných päť záloh.

**Bežiace nasadenie je vidieť aj po návrate na stránku.** Keď si počas nasadenia odišiel na inú záložku a vrátil
sa, riadok na stránke UAT či PROD ukazoval starý stav a svietiace „Nasadiť“, akoby sa nič nedialo. Teraz sa riadok
sám pýta kokpitu, či na inštalácii beží nasadenie: ukáže krok a ako dlho trvá, „Nasadiť“ aj „Prevziať inštaláciu…“
sú dovtedy zašednuté a po skončení sa riadok sám obnoví na výsledok. Platí to aj pre nasadenie spustené z iného okna.

**Celá verzia či rýchla oprava môže byť darom.** Doteraz sa dalo za dar — pozornosť ICC — označiť len jednotlivú
úlohu; pri verzii a rýchlej oprave si si vyberal len medzi opravou chyby v dodanom kóde a zmenou. Teraz je pri
druhu práce tretia možnosť „Dar — pozornosť ICC“: úlohy takej práce sú na súpise dodaných tokenov v kategórii Dar,
súpis ukáže, akú mala hodnotu, a zľava je tá z Nastavení (predvolene 100 %). Tie isté tri druhy práce — Oprava (BUG),
Dar (GIFT), Požiadavka (CHR) — nesú od tejto verzie aj tikety v Plane, ktoré zakladá kokpit.
