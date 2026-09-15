# v4.39.2 — prevzatie inštalácie nechá sieti jej meno

Druhá oprava toho istého kroku, tentoraz nájdená v ostrej prevádzke.

## Prečo

Verzia v4.39.1 zariadila, že prevzatie prenesie priečinky, ktoré patria samotnej inštalácii. Pri
prvom skutočnom prevzatí sa ukázalo, že to nestačí: prevzatie prebehlo, ale nasadenie hneď po ňom
spadlo.

Nasadenie totiž sieť **premenúva** — volá ju tak, ako ju volá zdrojový projekt, nie tak, ako sa volá
v bežiacej inštalácii. Adresu siete sme prenášali, meno nie. Vznikla teda požiadavka na novú sieť
s adresou, ktorú už drží tá stará, a Docker ju odmietol.

Appka pritom celý čas bežala ďalej — nasadenie spadlo skôr, než čokoľvek zhodilo.

## Čo to znamená pre teba

**Prevzatie nechá sieti jej pôvodné meno.** Nasadenie hneď po prevzatí už neskončí hláškou
o prekrývajúcich sa adresách.

Meno siete je rovnaký údaj o inštalácii ako jej adresa — premenovať ho pri prevzatí nemá dôvod:
stará sieť by osirela a nová by sa s ňou bila o to isté miesto.

Keď sa mená zhodujú, nemení sa nič.
