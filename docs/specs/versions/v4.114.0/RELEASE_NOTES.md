# v4.114.0

**Každé meno služby inštalácie vedie len k jej vlastnej službe.** V spoločnej sieti stroja už kokpit od verzie
4.112.0 viaže frontend na backend jeho inštalácie. Backend však ďalej videl meno „frontend“ na všetkých inštaláciách
v sieti. Teraz kokpit pri nasadení naviaže každú službu v spoločnej sieti aj na ostatné služby jej inštalácie, ktoré tam
sú — backend na frontend, pri webovom projekte aj frontend a web navzájom. Inštalácia sa tým opraví pri najbližšom
nasadení a kokpit to raz oznámi. Kontrola po nasadení teraz overí každé také meno, nielen meno backendu.
