# v4.52.2

**Nasadenie na iný stroj už nezahltí linku na štvrťhodinu.** Kokpit doteraz pri stavbe posielal na cieľový stroj celý
pracovný priečinok projektu — aj vyrovnávacie pamäte a pomocné súbory agenta — a pri NEX Inboxe ho poslal trikrát,
spolu okolo 600 MB. Teraz stavia len zo súborov overeného commitu v gite a každý obraz postaví raz; pri NEX Inboxe
pôjde na MAGER okolo 50 MB. Na cieľ sa tak nedostane nič, čo overenie nevidelo, a v správe o nasadení uvidíš, z ktorého
commitu sa stavalo.
