# v4.182.0

**Sprievodca v aplikáciách na šablóne.** Aplikácia na šablóne aplikácií (nex-app-kit od 0.24.0) má Sprievodcu:
v „Ako urobiť“ človek napíše, čo chce urobiť, a Sprievodca ho krok za krokom dovedie k cieľu — kliká vždy človek.
Kokpit mu teraz kroky do aplikácie dopĺňa a stráži ich:

- Na stránke projektu je panel **Sprievodca v aplikácii**. Predvolene je zapnutý; pri špeciálnej aplikácii ho
  vypneš tlačidlom „Vypnúť Sprievodcu“ (zmena sa zapíše do manifestu aplikácie a uloží do gitu projektu). Projekt na
  staršej šablóne sa dozvie, od ktorej verzie Sprievodcu má.
- Kým zapnutý Sprievodca nemá úplný katalóg krokov, je to **riadna položka verzie**: Špecifikácia má časť „Ciele
  používateľov“ — čo chce človek v aplikácii urobiť — a Staviteľ z nej pripraví kroky.
- **Pred Overením** kokpit skontroluje, že každá obrazovka z ľavého panela má krok, že tlačidlo každého kroku
  v aplikácii naozaj je a že žiadne tlačidlo neukazuje na krok, ktorý nie je. Čo chýba, povie vetou.

Nové aplikácie dostanú katalóg krokov rovno v kostre (`frontend/src/guide.ts`).
