# v4.172.3

**Opakované nasadenie pripojí aj službu, ktorá doteraz ostala bez siete.** Verzia 4.172.2 pripájala služby, ktoré
čítajú NEX Knowledge alebo NEX Manager, na sieť stroja len pri prvom nasadení. „Nasadiť znova“ však predpis bežiacej
inštalácie nevykresľuje nanovo, len v ňom zmení verziu, a preto pracovník NEX HelpDesk ostal bez spojenia. Teraz
opakované nasadenie pripojí takú službu na siete jej backendu a v hlásení nasadenia povie, ktoré služby pripojilo.

**Kontrola po nasadení sa nepýta krokov, ktoré už skončili.** Kontrola čítania NEX Knowledge sa pýtala aj migrácie
databázy, ktorá po nasadení už nebeží, a hlásila „nedalo sa overiť“ namiesto toho, čo povedali backend a pracovník.
Teraz číta len zo služieb, ktoré bežia stále.
