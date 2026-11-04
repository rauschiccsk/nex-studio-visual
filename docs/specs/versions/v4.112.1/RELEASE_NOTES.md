# v4.112.1

**Kontrola po nasadení vidí všetky adresy backendu.** Kokpit po nasadení overuje, kam z frontendu vedie meno backendu;
pýtal sa však spôsobom, ktorý vráti len prvú adresu, kým nginx frontendu použije všetky. Teraz sa pýta na všetky, takže
upozorní aj vtedy, keď cudzí backend nie je medzi adresami prvý.
