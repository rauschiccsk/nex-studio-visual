# v4.96.2

**Opakované nasadenie doplní adresu databázy, ktorú inštalácii nechalo prázdnu skoršie nasadenie.** Pri opakovanom
nasadení sa nastavenia inštalácie zámerne nemenia, takže inštalácia, ktorú pokazilo nasadenie pred opravou 4.96.1,
zostávala pokazená (Author Asistent na UAT). Teraz kokpit doplní prázdne nastavenie pripojenia k databáze, ktoré
služby potrebujú, s tým istým menom, heslom a databázou, aké inštalácia má, a povie to vo varovaní nasadenia.
Ostatné nastavenia inštalácie sa nemenia. Meno, heslo a názov samotnej databázy sa nikdy nedopĺňajú. Keď adresu
doplniť nejde, nasadenie sa zastaví ešte pred zápisom. Kontrola prázdnej adresy sa pozerá na nastavenia, s ktorými
inštalácia naozaj pobeží.
