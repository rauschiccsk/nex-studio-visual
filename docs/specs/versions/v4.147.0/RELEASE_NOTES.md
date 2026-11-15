# v4.147.0

**Aplikácia na šablóne môže hovoriť viacerými jazykmi — na začiatok slovensky a maďarsky.** Pri novom projekte so
šablónou NSV štandard vyberieš hlavný jazyk aplikácie a ďalšie jazyky; pri existujúcom projekte ich zmeníš v Nastaveniach
projektu. Každý človek si potom v aplikácii vyberie svoj jazyk sám, takže tú istú aplikáciu môžu súčasne používať ľudia
v rôznych jazykoch. Na obrazovke Zákazníci určíš jazyk inštalácie — ním aplikácia u zákazníka hovorí, kým si človek
nevyberie svoj; platí od najbližšieho nasadenia.

Agent stavia obrazovky tak, že každý text je v katalógu každého jazyka projektu, a kokpit to pri Overení zmeria: verzia,
ktorej v niektorom jazyku chýba preklad alebo má text napísaný priamo v kóde, neprejde — kokpit povie, ktorý text
a v ktorom jazyku chýba. Projekt, ktorý stojí na staršej šablóne aplikácie, ju pred uložením jazykov povýši tlačidlom
priamo pri jazykoch. Aj Projektant v Nápadoch vie, ktorými jazykmi môže nová aplikácia hovoriť, a zapíše ich do zadania
nového projektu. Aplikácie, ktoré hovoria len po slovensky, ostávajú bez zmeny.
