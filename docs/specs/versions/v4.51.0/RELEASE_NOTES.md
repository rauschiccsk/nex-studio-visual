# v4.51.0

**Prázdny plán úloh povie, kedy plán vznikne.** Pri stavbe novej verzie po fázach (Príprava → Návrh → Vizuál →
Programovanie) vzniká plán úloh až na začiatku Programovania — zo schváleného Návrhu a zmien vo Vizuáli. Panel
„Plán úloh“ to teraz aj tak povie; doteraz písal, že sa plán objaví po dohode na špecifikácii, hoci tá bola dávno
schválená. Pri rozhovorovej stavbe a pri rýchlej oprave hovorí ich vlastnú vetu.

**Z tiketu v Plane rovno do Zásobníka alebo do rýchlej opravy.** V záložke „Plane tikety“ má každý otvorený tiket,
ktorý ešte nikam nešiel, dve tlačidlá. „Do Zásobníka“ z neho urobí požiadavku REQ-N prepojenú s tiketom — tiket sa
potom posúva sám ako tiket založený zo Zásobníka a do komentára sa zapíše, ako požiadavka vznikla. „Spustiť rýchlu
opravu“ otvorí zadanie zložené z názvu a textu tiketu s vopred zvoleným druhom práce; po úprave a kliknutí vznikne
opravná verzia ako doteraz. Tiket rýchlu opravu sleduje: pri spustení prejde do In Progress, pri nasadení na UAT
dostane komentár a po nasadení na PROD sa uzavrie (Done) s verziou, zákazníkom a dátumom. Jeden tiket ide jednou
cestou — tiket v Zásobníku rýchlu opravu nespustí a z tiketu s rýchlou opravou nevznikne požiadavka. Keď Plane
neodpovie, oprava aj požiadavka ostanú a kokpit povie vetou, čo sa do tiketu nezapísalo.
