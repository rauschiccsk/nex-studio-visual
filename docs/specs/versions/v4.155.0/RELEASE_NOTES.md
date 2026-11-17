# v4.155.0

**Aplikácia, ktorá číta NEX Knowledge zákazníka, dostane jej adresu od kokpitu.** Kokpit pri nasadení zapíše adresu
NEX Knowledge toho istého zákazníka na tom istom serveri — priamo, bez proxy, ako to NEX Knowledge vyžaduje — a po
nasadení raz z aplikácie skúsi čítanie. Výsledok povie: aplikácia číta, chýba jej kľúč aplikácie, kľúč neplatí, alebo
NEX Knowledge u zákazníka ešte nie je nasadená.

**Úzky verejný vstup cez tunel Cloudflare.** Aplikácia môže vyhlásiť cesty, ktoré majú ísť na internet (napríklad
pre hlasovú platformu a telefónneho operátora pri NEX HelpDesku). Kokpit pri nasadení sám zriadi tunel Cloudflare,
pustí ním len tieto cesty, všetko ostatné odmietne ešte pred aplikáciou a po nasadení to overí zvonku. Obrazovky
aplikácie ostávajú len v Tailscale. V nastaveniach zákazníka je vidieť, ktoré cesty pôjdu von.
