# v4.0.78 — NEX Studio Visual

## Dokumenty už neučia nesprávny postup

Postup má **päť fáz** — Príprava, Návrh, **Vizuál**, Programovanie, Verifikácia. Viacero dokumentov ich uvádzalo štyri a Vizuál v nich chýbal úplne. Pritom je to práve tá fáza, ktorá stavbu zastaví a počká na tvoje rozhodnutie — kto o nej nevie, prečíta si to zastavenie ako poruchu.

Opravené na všetkých miestach: pravidlá zapisované do každého nového projektu, príručka „Ako začať", obrazovky projektu a verzie, aj obe charty agentov. Pribudla kontrola, ktorá **porovnáva dokumenty priamo s motorom**, takže rozdiel sa už nemôže potichu vrátiť.

Zmizli aj pokyny na postup, ktorý si zrušil, a charta roly, ktorá už neexistuje.

## Kontroly, ktoré nikto nespúšťal

Kontrola štýlu kódu bola nastavená a **nespúšťal ju nikto** — hoci ju kokpit vnucuje každému projektu, ktorý založí. Teraz beží pri každej zmene. Zdravotná kontrola po nasadení zahadzovala vlastný výsledok, takže nemohla nikdy zlyhať.

Predcommitové poistky boli v repozitári, ale v pracovnej kópii vypnuté — zapnuté a overené skutočnou chybou.

## Obrazovky hovoria pravdu o právach

Tlačidlo „Nasadiť" vyzeralo živo aj pre toho, kto na to nemá právo, a skončilo zamietnutím. Teraz je zašednuté a povie prečo — a to na oboch záložkách, nie len na jednej.

Karta „Relácie" ukazovala namiesto mena používateľa jeho vnútorný identifikátor, takže sa rozhodovalo o zrušení prístupu bez toho, aby bolo jasné komu.

„Dokumenty" tvrdili, že projekt žiadne nemá, kým nie je vybraná verzia — aj keď ich mal desiatky.

## Infraštruktúra, ktorá bola zapojená, ale nefungovala

Denníky agenta sa zapisovali inam, než sa ukladali — pri každom reštarte sa strácali. Izolované prostredie Konzultácie sa nikdy nespustilo a mlčalo o tom. Telegramové upozornenia sa nedali overiť, lebo skript hlásil úspech vždy.
