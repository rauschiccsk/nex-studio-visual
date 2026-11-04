# v4.110.0

**Nasadenie novej verzie doplní do inštalácie službu, ktorú verzia priniesla.** Doteraz opakované nasadenie menilo
v inštalácii len verziu, takže nová služba (napríklad krok NEX Knowledge 0.2.1, ktorý pred štartom odovzdá súbory
aplikácii) do nej nikdy neprišla a kokpit len radil „doplň ich do inštalácie“. Teraz kokpit podľa histórie projektu
spozná, ktorú službu priniesla nová verzia, a doplní ju presne tak, ako ju zapisuje do novej inštalácie; služby, ktoré
na ňu čakajú, na ňu čakajú aj tu a nič iné sa v inštalácii nemení. Pri nasadení je napísané, čo pribudlo. Službu,
ktorú inštalácia nemá odjakživa (napríklad databázu vedenú inde), ani inštaláciu s vlastnými menami služieb či úložísk
kokpit nemení a povie, prečo službu nedoplnil.
