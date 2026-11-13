# v4.138.1

**Skúška nasadenia pri Overení už neskúša inštaláciu, do ktorej by nasadenie nikdy nezapísalo.** Overenie pred
Hotovo skúša nasadenie nanečisto do novej inštalácie aj do každej existujúcej UAT inštalácie projektu. Brala však aj
ručne písanú inštaláciu, ktorú kokpit nespravuje — skutočné nasadenie ju odmietne prepísať, a tak ju skúška odmietla
tiež a Overenie zlyhalo, hoci aplikácia bola v poriadku (NEX Manager 1.6.0). Teraz skúška berie len inštalácie, do
ktorých kokpit naozaj nasadzuje, a ručne písanú vo výsledku pomenuje s vetou, prečo sa neskúšala.
