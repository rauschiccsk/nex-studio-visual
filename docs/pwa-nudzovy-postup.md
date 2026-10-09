# Núdzový postup — keď sa niekomu zasekne stará verzia

> Robíme všetko preto, aby na tento dokument nikdy nedošlo. Keď na neho dôjde, bude to
> pod tlakom — preto sú tu **kroky, nie vysvetľovanie**.

---

## 1. Je to naozaj stará verzia?

Väčšina hlásení „kokpit mi nefunguje" starou verziou nie je. Rozoznáš to podľa jednej otázky:

> **„Vidíš tú vec, ktorú sme práve nasadili? A vidí ju kolega vedľa teba?"**

| Čo človek hovorí | Čo to pravdepodobne je |
|---|---|
| **Ja starú, kolega novú** | naozaj stará verzia — pokračuj bodom 2 |
| **Obaja starú** | nenasadilo sa; skontroluj nasadenie, nie kokpit |
| Kokpit hlási chybu, ale verzia sedí | iná porucha — sem nepatrí |
| „Nič sa nedeje, točí sa to" | výpadok siete alebo servera — sem nepatrí |

**Číslo verzie je vľavo hore pod názvom NEX Studio Visual** (v bočnom paneli). Nech ti ho človek prečíta a porovnaj s tým,
čo sme nasadili. Keď sedí, stará verzia to nie je.

---

## 2. Čo si vypýtať

Štyri veci, viac netreba:

- **Číslo verzie** vľavo hore pod názvom (odfotené mobilom stačí).
- **Má kokpit nainštalovaný, alebo ho otvára v prehliadači?** (Nainštalovaný = spúšťa ho
  z ikony na ploche a nemá hore adresný riadok.)
- **Aký prehliadač** a či na Windows alebo Macu.
- **Kedy to začalo** a či to iní ľudia vidia tiež.

---

## 3. Rýchla pomoc pre jedného človeka

Vyskúšaj v tomto poradí. Väčšina prípadov skončí na prvom kroku.

1. **Zavrieť kokpit úplne a otvoriť znova.** Nie prepnúť na inú kartu — zavrieť okno.
   Kokpit sa pri každom spustení pýta servera na novšiu verziu, takže toto obvykle stačí.
2. **Podržať `Ctrl` a kliknúť na obnovenie** (na Macu `Cmd`). Načíta stránku bez toho,
   aby použila čokoľvek odložené.
3. **Odinštalovať a nainštalovať znova:**
   - V okne kokpitu klikni na **tri bodky vpravo hore → Odinštalovať NEX Studio Visual**.
   - Ak tam tá možnosť nie je: v prehliadači otvor `chrome://apps` (v Edge `edge://apps`),
     klikni pravým na NEX Studio Visual a daj **Odstrániť**.
   - Potom otvor kokpit v prehliadači (`https://studio.int.isnex.eu`) a nainštaluj ho nanovo (ikona inštalácie v adresnom riadku).

Keby ani to nepomohlo, alebo to hlási **viac ľudí naraz**, choď na bod 4.

---

## 4. Odpojenie u jedného človeka (technický krok)

Toto robí niekto, kto sa nebojí nástrojov pre vývojárov:

1. V kokpite stlač **`F12`** (na Macu `Cmd+Option+I`).
2. Karta **Application** → v ľavom stĺpci **Service Workers**.
3. Klikni **Unregister**.
4. Vedľa v **Storage** klikni **Clear site data**.
5. Zavri okno kokpitu a otvor ho znova.

---

## 5. ZÁCHRANNÁ BRZDA — odpojenie u VŠETKÝCH NARAZ

**Kedy:** keď je jasné, že problém je v mechanizme inštalovateľnosti a týka sa viacerých ľudí.
Nečakaj, kým obvoláš každého — nemuseli by ste sa dovolať všetkých.

**Čo to spraví:** kokpit sa u všetkých ľudí sám odpojí od toho mechanizmu, vyčistí si všetko
odložené a odvtedy beží ako obyčajná stránka. **Nikto nemusí na svojom počítači nič robiť.**

**Čo to stojí:** kokpit sa dovtedy nedá nainštalovať. Je to ústup do bezpečia, nie oprava.

### Kroky

1. Skopíruj pripravený súbor cez ostrý:

   ```
   cp docs/pwa-brzda/sw.js frontend/public/sw.js
   ```

2. Commitni, počkaj na zelenú kontrolu na GitHube a nasaď kokpit ako každú inú zmenu
   (`scripts/deploy-prod.sh all` — obrazovka sa pečie do obrazu frontendu).

3. **Nič ďalšie sa nerobí.** Ľudia dostanú brzdu pri najbližšom otvorení kokpitu —
   otvorené okná sa navyše načítajú samy, takže to platí hneď, nie až po reštarte.

4. Overenie, že to zabralo: požiadaj niekoho, nech otvorí kokpit a v `F12 → Application →
   Service Workers` uvidí prázdno.

### Návrat späť

Keď je príčina odstránená, vráť pôvodný súbor (`git revert` toho commitu) a nasaď.
Kokpit sa opäť dá nainštalovať.

---

## Prečo toto všetko existuje

Zadanie NEX Managera, z ktorého kokpit tento mechanizmus prevzal (DEV-21), hovorí o mechanizme
inštalovateľnosti:

> *„Zle nastavená inštalovateľná appka vie podávať starú verziu aj niekoľko dní a používateľ
> sa jej nemá ako zbaviť — ani reštartom, ani odhlásením."*

Kokpit je postavený tak, aby na to nedošlo: **sám sa neukladá**, takže starú verziu nemá odkiaľ
vziať, a pri každom štarte sa pýta servera. Brzda je poistka na prípad, že by sme sa napriek
tomu mýlili — aby platilo, že **zbaviť sa toho vieme my, u všetkých naraz**.
