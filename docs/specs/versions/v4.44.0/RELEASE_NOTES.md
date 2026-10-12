# v4.44.0

**Čo si v UAT vyskúšal, si kokpit pamätá.** Pri každej zmene v paneli „Čo otestovať vo verzii“ ju odškrtneš ako
„Vyskúšané“ a hore vidíš „Otestované 3 z 5“. Pri viacerých zákazníkoch si vyberieš, u koho skúšaš. Kokpit si to
pamätá na serveri, takže to platí aj na inom počítači. Keď zmena nefunguje, klikneš na „Nefunguje“, napíšeš, čo sa
stalo, a „Pripraviť rýchlu opravu“ ťa prenesie do okna Rýchla oprava s hotovým zadaním. Spustíš ho sám, ako každú
rýchlu opravu. Pri „Akceptovať“ ti kokpit povie, koľko je otestované a čo ostalo, a zapíše to k akceptácii.

**Každá úloha má na faktúre svoju kategóriu a každá kategória svoju zľavu.** Pri každej úlohe v Pláne úloh vyberieš,
či je to požiadavka zákazníka, oprava chyby v kóde, ktorý sme už dodali, alebo dar — pozornosť ICC. Nastaviť ju môžeš
aj naraz celej funkcii alebo celku. AI Agent kategóriu navrhne už pri plánovaní a úloha bez nej sa ráta podľa verzie.
Rýchla oprava chyby sa ráta ako oprava, všetko ostatné ako požiadavka. Súpis dodávky teraz rozdelí dodaný kód podľa
toho, ktorá úloha ho napísala. Každý riadok ukáže plnú hodnotu, zľavu a sumu, takže zákazník vidí, čo dostal ako
záručnú opravu alebo ako dar. Predvolené zľavy sú v Nastaveniach: oprava chyby 100 % (záručná oprava), dar 100 %,
požiadavka 0 %. Pre jednu verziu ich zmeníš priamo v súpise a kokpit si zapamätá, kto a kedy ich menil. Vydaný súpis
sa už nemení — nové kategórie a zľavy platia až v súpise, ktorý ho nahradí.

**Ľavý panel je hustejší a šírku panelov si nastavíš sám.** Záložky v ľavom paneli sú nižšie a nadpis nad
Nastaveniami nahradila čiara. Logo a pätička sú v jednom riadku, takže na bežnej obrazovke počítača sa celý panel
zmestí bez posúvania a ostane miesto na ďalšie záložky. Prepínač „Pri počítači / Preč“ je zelená bodka alebo mesiac
vedľa tvojho mena. Pravý okraj ľavého panela a ľavý okraj Plánu úloh sa dajú potiahnuť myšou alebo posunúť šípkami.
Dvojklik vráti predvolenú šírku. Šírky aj zbalený ľavý panel si kokpit pamätá pri tvojom účte, takže ich máš rovnaké
na každom počítači a každý používateľ má vlastné. Na mobile a tablete sa nemení nič.

**Nová záložka Poznámky — rýchle poznámky, kým ich nevyriešiš.** V ľavom paneli hore je záložka Poznámky. Poznámka je
obyčajný text a jej názvom je prvý riadok. Ukladá sa sama počas písania a pod textom vidíš, kedy sa uložila. Poznámky
vidíš len ty, nikto iný, ani správca, ani agent, ani Poradca. Máš ich na každom počítači aj na mobile. Keď poznámku
medzitým zmeníš na inom zariadení, kokpit tvoj text potichu neprepíše: ukáže ti oba a vyberieš, ktorý ostane.
„Vyriešené“ presunie poznámku medzi vyriešené, odkiaľ sa dá vrátiť; natrvalo ju zmažeš len výslovne. „Do Zásobníka“
z nej spraví požiadavku REQ vo vybranom projekte (prvý riadok je názov, zvyšok popis) a poznámka na ňu odkáže.

**Kód každej hotovej úlohy je hneď na GitHube a kontroly na ňom bežia počas celej stavby.** Doteraz kokpit odoslal
hlavnú vetvu projektu na GitHub až po úspešnej Verifikácii. Kontroly na GitHube tak dodávaný kód videli až na konci
a GitHub ukazoval niečo iné ako kokpit. Teraz ju kokpit odošle po každej hotovej úlohe. Keď sa odoslanie nepodarí,
napíše ti to v rozhovore aj s dôvodom a kód odošle spolu s ďalšou úlohou. Riadok Zostavenie namiesto „zatiaľ nevieme“
povie, že kód na GitHube ešte nie je a koľko commitov čaká na odoslanie. AI Agent hlavnú vetvu sám neodosiela; na
GitHub posiela len bočné vetvy, keď potrebuje dokázať, že skúška vie zlyhať.

**Nová záložka Repozitár — behy kontrol projektu bez otvárania GitHubu.** V ľavom paneli pod Dokumentmi je záložka
Repozitár. Ukáže behy kontrol (CI) vybraného projektu tak ako GitHub: stav slovami (Prešlo, Zlyhalo, Beží, Čaká,
Zrušené), predmet commitu, číslo behu, vetvu, kto ho spustil, kedy a ako dlho bežal. Pri zlyhanom behu vidíš aj krok,
ktorý zlyhal. Pri behoch spustených ručne, kde GitHub píše len „CI“, ukáže predmet commitu, ktorý sa kontroloval.
Číslo úlohy v commite je tlačidlo: otvorí Plán úloh danej verzie, rozbalí úlohu a zvýrazní ju. Commit, ktorý beží
u zákazníka, má značku „na PROD“ alebo „na UAT“ s menom zákazníka a verziou. Behy filtruješ podľa stavu, vetvy
a človeka a môžeš v nich hľadať. Záložka sa obnovuje sama každých 30 sekúnd. Kokpit sa GitHubu pýta raz za všetkých,
nech je otvorených okien koľkokoľvek. Záložka behy len ukazuje, nespúšťa ani nezastavuje ich.
