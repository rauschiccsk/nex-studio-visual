## v3.0.167 — UAT inštancie sa pomenúvajú po projekte

UAT inštancia zákazníka sa doteraz pomenúvala len podľa **zákazníka a prostredia** (`andros-uat`) — chýbal v tom projekt. To znamenalo mätúci názov a riziko, že dva rôzne projekty toho istého zákazníka by si na jednom UAT prekážali. Odteraz sa UAT pomenúva **po projekte** — `uat-<zákazník>-<projekt>` (napr. `uat-andros-payables`), presne ako PROD. Každý projekt tak má vlastný, jasne pomenovaný UAT. (Zároveň opravený odkaz na PROD v prehľade nasadení, ktorý predtým ukazoval na nesprávnu adresu.)
