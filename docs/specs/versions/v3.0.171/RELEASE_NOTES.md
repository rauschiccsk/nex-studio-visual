## v3.0.171 — Prihlásenie do nasadenej appky pozná manažér

Keď NEX Studio nasadilo appku, admin používateľ dostal **náhodné heslo, ktoré nikto nevidel** — manažér sa tak nevedel prihlásiť do vlastnej appky. Odteraz je počiatočné heslo admina **tajomstvo, ktoré manažér sám nastaví pri zákazníkovi** (pole „Secret" v Zákazníci). Prihlásenie do nasadenej appky je teda **`admin` + toto tajomstvo** (a appka pri prvom prihlásení vyžiada zmenu hesla). Ostatné technické tajomstvá appky ostávajú náhodné, ako majú byť.
