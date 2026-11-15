# v4.141.3

**Povýšenie šablóny aplikácie posunie server vo všetkých súboroch, z ktorých sa stavia.** Niektoré projekty (NEX
Knowledge) stavajú server zo súboru requirements.txt. Kokpit pri povýšení šablóny pri založení verzie prepísal len
pyproject.toml a frontend, takže server by sa postavil so starou šablónou a frontend s novou. Teraz posunie šablónu aj
v requirements.txt a zapíše ju v tom istom uložení. Keď je v ňom šablóna zapísaná tak, že ju kokpit prepísať nevie,
povýšenie odmietne a nič nezmení. Stav šablóny na stránke projektu už neukáže verziu servera, keď sa tieto súbory
nezhodujú.
