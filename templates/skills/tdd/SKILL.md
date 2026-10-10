---
name: tdd
description: Postup červená → zelená → upratanie pri písaní kódu so správaním, ktoré sa dá overiť skúškou (nový endpoint, služba, validačné pravidlo, hraničný prípad, oprava chyby). Použi na začiatku každej takej úlohy, ešte pred prvým riadkom kódu.
---

# Skúška najprv — červená, zelená, upratanie

Kód, ktorý nesie správanie, začína skúškou. Nepoužívaj pri čistom presune kódu bez zmeny správania,
pri zmene jedného nastavenia, pri dokumentácii a pri úprave vzhľadu bez overiteľného správania.
Postup beží **vnútri** úlohy, ktorú máš zadanú — nie je to dôvod meniť jej rozsah.

## 🔴 Červená — najprv skúška, ktorá padne

1. Nájdi správny modul skúšok (služba → `tests/…/test_<služba>.py`, router → skúška cez HTTP klienta;
   frontend → skúška komponentu vo `frontend/src/__tests__/`).
2. Napíš **jednu** skúšku, ktorá zachytí nové očakávané správanie. Over výsledok, nie postup implementácie.
   Použi existujúce prípravky (`conftest.py`), nevymýšľaj vlastné.
3. Spusti ju a **over, že padne — a padne na tom, čo stráži**, nie na preklepe v prípravku či importe.
   Skúšky backendu spúšťaj podľa zručnosti `backend-tests`.
4. Keď prejde hneď, chyba je v skúške, nie v kóde — sprísni ju.

## 🟢 Zelená — najmenšia zmena, ktorá skúšku pustí

1. Urob najmenšiu zmenu, po ktorej skúška prejde; nerozširuj nad to, čo skúška žiada.
2. Spusti skúšku znovu — musí prejsť. Potom okolité skúšky modulu — žiadna iná nesmie padnúť.

## 🧼 Upratanie — pod ochranou skúšok

1. Mená, vytiahnuté pomocné funkcie, typy, duplicita. Po každej úprave skúšky znovu; pri červenej vráť
   posledný krok a skús menší.
2. Skonči, keď je kód čistý — predčasnú abstrakciu nerob.

## Pred commitom

- Celá sada skúšok backendu, `ruff format . && ruff check .` v `backend/`, `npm run type-check` a
  `npm run lint` vo `frontend/` — presne ako CI.
- Skúška, ktorá nemôže sčervenať, nič nedokazuje: ak si ju nevidel padnúť, nevieš, či stráži.
- V tele commitu stručne uveď skúšku, ktorá zmenu drží (`Skúška: tests/…::test_… (červená → zelená)`).
