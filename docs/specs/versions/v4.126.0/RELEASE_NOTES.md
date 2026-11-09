# v4.126.0

**Po nasadení kokpit overí, ktorú verziu aplikácia o sebe hlási.** Kokpit sa po každom nasadení (UAT aj PROD, aj na inom
stroji) opýta aplikácie na jej verziu v kontrole zdravia a porovná ju s nasadenou. Keď sa líšia, pri riadku zákazníka
povie obe čísla — beží iná verzia, než kokpit nasadil (starý obraz alebo číslo verzie zapísané v kóde). Aplikácia, ktorá
verziu nehlási, to povie vetou. Nasadenie neprepadne — aplikácia beží; upozornenie má zastaviť akceptáciu či ďalšie
nasadenie, kým sa to nevysvetlí. Doteraz to nikto nekontroloval: Author Asistent 1.0.0 v PROD TATA hlási 0.1.0.
