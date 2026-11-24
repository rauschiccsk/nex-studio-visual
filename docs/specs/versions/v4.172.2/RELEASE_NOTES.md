# v4.172.2

**Každá služba aplikácie, ktorá číta NEX Knowledge, sa k nej dostane.** Kokpit doteraz pripájal na sieť stroja len
frontend a backend aplikácie. NEX HelpDesk číta vedomosti v pracovníkovi (worker), ktorý ostal len vo vlastnej sieti
inštalácie, a v „Stav spojení“ hlásil, že NEX Knowledge je nedostupná. Teraz kokpit pripojí na sieť stroja každú službu,
ktorá dostáva adresu NEX Knowledge alebo NEX Manager. Na tejto sieti dostane len spojenie, nie vlastnú verejnú adresu.
Inštalácia s vlastným smerovaním dostane siete, na ktorých je jej backend. Zmena sa prejaví pri najbližšom nasadení
aplikácie.

**Nasadenie overí čítanie z každej takej služby.** Kontrola po nasadení čítala NEX Knowledge len z backendu, a preto
odpojený pracovník neprekážal. Teraz skúša čítanie z každej služby, ktorá má adresu NEX Knowledge, a keď niektorá
nedosiahne, povie jej meno.
