## v3.0.165 — Nasadenie overí verejnú dostupnosť (už neklame „✓ Nasadené")

Doteraz nasadenie overovalo len to, či appka odpovedá **vnútri** (v kontajneri) — nie či je naozaj dostupná na **verejnej adrese**. Preto sa mohlo stať (a stalo), že cockpit hlásil **„✓ Nasadené", hoci stránka bola zvonku nedostupná** (pokazené smerovanie).

Odteraz nasadenie na konci **overí presne tú adresu, ktorú vidí zákazník** (cez Traefik s reálnou doménou). Ak appka na verejnej adrese neodpovedá, nasadenie **čestne oznámi zlyhanie** aj s dôvodom — namiesto falošného úspechu. (Ak sa smerovanie z technických príčin nedá overiť, radšej sa preskočí, než by hlásilo falošný poplach.) Doplnené testy.
