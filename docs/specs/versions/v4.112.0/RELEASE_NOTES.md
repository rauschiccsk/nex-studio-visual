# v4.112.0

**Frontend aplikácie trafí vždy svoj vlastný backend.** Na serveri sú aplikácie všetkých zákazníkov v jednej spoločnej
sieti a ich backendy tam majú rovnaké meno „backend“; pri požiadavke cez frontend preto mohla odpovedať aplikácia iného
zákazníka. Kokpit teraz pri každom nasadení naviaže frontend inštalácie na jej vlastný backend a po nasadení na serveri
overí, že meno backendu nevedie nikam inam — inak to napíše pri nasadení. Existujúca inštalácia sa opraví najbližším
nasadením a kokpit to pri ňom raz oznámi.
