# v4.30.0 — šesť opráv z jednej stavby

Všetkých šesť vzniklo počas jedinej stavby NEX Managera 1.1.0. Ani jedna od stola:
každá je vec, na ktorú Director klikol a nezachovala sa, ako mala.

Tri z nich patria do rovnakej rodiny — oprava zapojená do jednej cesty z viacerých.
Je to najčastejší tvar chyby, ktorý v NEX Studiu nachádzame.

## Pripravené zadanie vidieť hneď, nie až po zrážke

Keď v priečinku verzie zadanie už leží, kokpit ho ukázal — ale až keď naň manažér
narazil. Musel najprv napísať vlastné, uložiť, dostať hlášku „zadanie už existuje", a
až vtedy sa objavilo tlačidlo „Prevziať toto zadanie do poľa".

Dovtedy nemal ako tušiť, že tam niečo je. Napísal vlastné a to pripravené si nevšimol.

Kokpit sa teraz spýta hneď pri zakladaní verzie a pripravené zadanie ukáže rovno. Iba
ukáže — do poľa nesiahne, prevziať ho je rozhodnutie manažéra. Poistka proti prepísaniu
zostáva nedotknutá.

## Úpravy hlavičky verzie sa už nezahodia

Keď sa prvé uloženie novej verzie nepodarilo, verzia už vznikla — a ďalšie úpravy polí
Číslo verzie, Názov a Cieľový dátum sa ticho zahodili, hoci sa dali ďalej písať.

Zmerané naostro: Director prepísal názov na „Inštalovateľná aplikácia PWA" a uložil.
V evidencii zostal pôvodný. Nikde sa to nepovedalo.

Pole, ktoré sa dá písať a nič nerobí, je horšie než pole zamknuté — človek si myslí, že
to, čo napísal, platí. Zmenené polia sa teraz uložia.

## Kolá opráv po Verifikácii sa počítajú

Manažér prešiel šiestimi kolami opráv a v kokpite nikde nestálo, koľké kolo to je.

Strop päť kôl existuje, ale ohraničuje **samočinnú** slučku medzi agentom a Audítorom —
teda prípad, keď sa tí dvaja točia dokola bez dozoru. Zakaždým, keď na kartu odpovie
človek, počítadlo sa nuluje, a to je správne: strop má brániť stroju bežiacemu naprázdno,
nie človeku, ktorý riadi dlhšiu opravu.

Diera bola inde: keď riadil človek, kolá sa nikde neukazovali. Karta teraz nesie číslo
kola — bez „z piatich", lebo taký strop tam neplatí a tvrdiť ho by bola lož. Od tretieho
kola sa navyše povie, že každé kolo stojí jeden beh agenta a jeden beh Audítora, nech sa
manažér vie rozhodnúť, či pokračovať, alebo zasiahnuť inak.

## Prevzatie už nezahodí vlastné pravidlá agenta bez zálohy

Pri prevzatí projektu sa vlastný `CLAUDE.md` odložil bokom, ale pravidlá v
`.claude/agents/` sa prepísali bez stopy. Auditorova charta NEX Managera sa tak zmenšila
zo 707 riadkov na 211.

Prepisovať ich je správne — staré pravidlá popisujú zrušený trojagentný svet a agent by
podľa nich pracoval inak, než kokpit riadi. Nekonzistentné bolo len to, že koreňová charta
zálohu dostala a rolové pravidlá nie. Nebolo to rozhodnutie: poistka bola napísaná pri
jednom zápise z troch. Teraz je to jedna poistka pre všetky.

*(Vtedy sa nič nestratilo iba preto, že súbor bol v gite. Pri projekte bez repozitára by
zmizol bez stopy.)*

## Čo sa pri zakladaní nedorobilo, sa už dá prečítať

Pri prevzatí kokpit zostavil vetu o tom, čo zámerne vynechal — CI, ochranu vetvy, skúšobné
spustenie — a nikto ju neuvidel. Žila len v odpovedi na založenie, a dialóg po úspechu
odchádza na stránku projektu, takže zanikla skôr, než sa dala prečítať.

Správa, ktorú nikto neprečíta, je to isté ako ticho — a práve tomu mala brániť.

Zoznam sa teraz drží pri projekte a stránka projektu ho ukazuje. Nie je to upozornenie na
odkliknutie, ale záznam o tom, ako projekt vznikol: platí, kým to niekto ručne nedorobí.

## Príznak živého náhľadu sa nedá zapnúť slovom „false"

Minulé vydanie zjednotilo kontrolu príznaku náhľadu na pravdivostnú. Nezávislá previerka
upozornila na druhú polovicu pravdy: v JavaScripte je pravdivý aj reťazec „false", takže
**vypnutie slovom „false" by náhľad zaplo**. Zvyk vypínať tak je pritom živý — inde
v hospodárstve sa presne takto používa.

Príznak teraz porovnávame s celým zoznamom zapínacích hodnôt (`1`, `true`, `yes`, `on`).
Rieši to oba protichodné omyly naraz: aj porovnanie s jediným slovom, aj pravdivostnú
kontrolu. Overené skutočným zostavením — v ostrom balíku po náhľade nezostáva ani stopa.

*(Nešlo o živú dieru — nikde to tak nastavené nebolo. Bola to zlá rada zapísaná do šablóny,
teda do každého budúceho projektu.)*
