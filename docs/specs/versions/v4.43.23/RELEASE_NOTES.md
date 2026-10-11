# v4.43.23

**Otvorené okno kokpitu ponúkne novú verziu hneď po nasadení.** Doteraz sa okno na novú verziu pýtalo len pri
načítaní, pri návrate zo zmenšenia a raz za 15 minút — okno, ktoré bolo celý čas na obrazovke, ukazovalo po nasadení
ešte štvrť hodiny starú obrazovku. Teraz sa spýta aj vtedy, keď naň prepneš, hneď ako sa po nasadení znovu spojí so
serverom, a inak každú minútu. Lišta „Je k dispozícii novšia verzia“ s tlačidlom „Načítať“ sa tak objaví do pár
sekúnd; počas práce sa okno samo neobnoví, aby nezahodilo rozpísaný text.

**Zdrojový kód aplikácie pre zákazníka je celý po anglicky.** AI Agent to má po novom v pokynoch pri každej úlohe
programovania aj vo Vizuáli: mená, komentáre a skúšky po anglicky, po slovensky len text, ktorý aplikácia ukáže
používateľovi. Verifikácia to kontroluje sama — keď verzia pridala do kódu slovenský komentár, popis alebo hlášku
skúšky, Verifikácia neprejde a povie, na ktorých miestach treba preložiť; „Nechaj to opraviť“ to agentovi odovzdá.
Texty obrazoviek a citáty v úvodzovkách kontrola nechá tak. Kód, ktorý verzia nemenila, sa nekontroluje.

**Nasadenie sa zastaví, keď inštalácia nie je pripravená na úložisko novej verzie, a povie, keď sa aplikácii po
nasadení niečo pokazilo.** Keď nová verzia ukladá dáta do priečinka, ktorý inštalácia u zákazníka nemá pripojený
(NEX Inbox 1.7.0 takto presunul zdieľaný priečinok pre NEX Genesis), nasadenie sa zastaví skôr, než čokoľvek
zmení, a povie, ktorý priečinok chýba — inštalácia beží ďalej na starej verzii. Doteraz nasadenie skončilo
zeleným „Nasadené“ a aplikácia potom hodiny zapisovala faktúry tam, kde ich nikto nenašiel. Po každom nasadení
sa kokpit navyše spýta aplikácie na jej zdravie a porovná ho so stavom pred nasadením: keď hlási problém, ktorý
predtým nemala (napríklad nedostupné zdieľanie), pod zákazníkom sa objaví upozornenie ešte pred akceptáciou.
