# v4.101.6

**Po každej úlohe Programovania ostáva aplikácia spustiteľná a kontroly na GitHube zelené.** Agent rozdelí plán tak,
že staré časti odstráni až úloha, ktorá ich nahrádza. Keď sa úloha nedá urobiť bez pokazeného medzistavu, opýta sa ťa,
ako ju rozdeliť. Pravidlo dostane aj stavba, ktorá práve beží, od svojej najbližšej úlohy.

Pokyny k prevodu existujúceho projektu na šablónu hovoria, že manifest aplikácie potrebuje aj obraz, v ktorom
kontroly na GitHube púšťajú skúšky — bez neho skúšky na GitHube padnú, hoci u agenta prejdú.
