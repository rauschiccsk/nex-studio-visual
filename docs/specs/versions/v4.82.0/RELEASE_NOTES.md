# v4.82.0

**Nasadenie kokpitu počká, kým dobehne rozrobená práca.** Nová verzia kokpitu sa pri nasadení reštartuje, a tým by
prerušila všetko, čo práve robí: ťah agenta (aj Verifikáciu, ktorá beží priamo v kokpite), odpoveď Poradcu či
nasadenie aplikácie zákazníkovi. Nasadenie sa teraz kokpitu najprv opýta, či v ňom niečo beží, a kým áno, reštart
odmietne s vetou, čo presne beží. Opýta sa ešte raz tesne pred reštartom, lebo príprava novej verzie trvá pár minút.
Nasadenie samotných obrazoviek nič neprerušuje, preto sa nepýta.
