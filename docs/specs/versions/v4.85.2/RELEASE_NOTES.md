# v4.85.2

**Projekt, ktorý má pre skúšky vlastnú databázu, sa dá ďalej programovať.** Keď si projekt pridá samostatnú
databázu na skúšky (napríklad `test-db` v NEX Manageri 1.4.0), kokpit ju pri programovaní agentovi podá spolu
s databázou aplikácie — doteraz každý ďalší krok odmietol. A keď kokpit agentovi prostredie podať nevie, povie to
ako svoju chybu, ktorú opraví náš technický tím, namiesto vety „Agent stratil spojenie / spadol“ s ponukou
pokračovať, ktorá nikam neviedla.
