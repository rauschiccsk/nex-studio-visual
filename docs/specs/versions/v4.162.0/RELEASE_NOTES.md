# v4.162.0

**Premenovanie na 1.0.0 prebehne pri akceptácii, ešte pred prvým PROD.** Keď akceptuješ UAT verzie, ktorá pôjde
ako prvá do ostrej prevádzky, kokpit ju hneď premenuje na 1.0.0, presunie jej poznámky k vydaniu a zmenu odošle na
GitHub. PROD sa potom postaví z tohto commitu, až keď jeho kontroly (CI) prejdú — kým bežia, Nasadiť počká a povie
prečo. PROD tak hlási 1.0.0 v čísle aj v Aktualizáciách. Predtým kokpit premenoval verziu až po nasadení a zmenu
odoslal bez kontroly — Orthodox Hermenia mala potom červené kontroly na hlavnej vetve. Verzia sa dá nasadiť aj pod
číslom, ktoré si videl pred akceptáciou.

**Staviteľ nepíše číslo verzie do kódu ani do skúšok.** Nové pravidlo v jeho charte.
