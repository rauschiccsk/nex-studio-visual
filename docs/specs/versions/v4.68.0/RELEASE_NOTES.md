# v4.68.0

**Poradca vidí všetko, čo vidíš ty v projekte.** Prečíta poznámky Audítora pri každom verdikte, celý popis
požiadavky v Zásobníku, verzie projektu, zákazníkov a ich nastavenia (heslá len ako „uložené“ alebo „chýba“),
nasadenia na UAT a PROD s hlásením a upozorneniami, tikety v Plane, náklady aj fakturáciu. Kokpit si odteraz
zapíše aj nasadenie, ktoré odmietol, s vetou, ktorú si dostal. Poradca ťa preto už nebude prosiť, aby si mu
hlásenie prekopíroval. Keď v kokpite pribudne nová obrazovka projektu, kontrola kokpitu nepustí, kým ju Poradca
nevie prečítať.

**Administrácia webového projektu po opakovanom nasadení pozná svoju adresu.** Keď inštalácia pri ďalšom nasadení
dostala web, adresa administrácie sa do nej zapísala z príkladu projektu, nie skutočná. Aplikácia potom odmietala
každé prihlásenie do administrácie ako požiadavku z cudzej adresy. Kokpit odteraz pri každom nasadení zapíše
adresy webu aj administrácie, ktoré určuje sám, na skutočné adresy inštalácie. Inštalácia s vlastným ručným
smerovaním si svoje adresy ponechá a hlásenie nasadenia povie, ktoré treba nastaviť ručne.
