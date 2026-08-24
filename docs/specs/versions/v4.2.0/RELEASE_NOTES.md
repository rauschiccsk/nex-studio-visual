# v4.2.0 — stavba sa už nedovolá na služby tohto počítača

Posledný kus série, ktorá zatvára stavbu do vlastného priestoru. Súbory boli
uzavreté už predtým; toto uzatvára **sieť**.

## Čo bolo otvorené

Pomocník, ktorý stavia projekt, musí vedieť volať von — bez internetu prácu
neurobí. Lenže tou istou linkou sa dalo dovolať aj **dovnútra**: na ktorúkoľvek
službu bežiacu na tomto počítači. Zmerané: odpovedalo **prihlásenie na stroj
(SSH)** aj rozhranie samotného NEX Studia.

Heslá k ním pomocník nemá. Ale „nemá heslo" nie je zámok, je to nádej — a
chránime sa pred **omylom**, ktorý žiadne heslo nepotrebuje.

## Čo sa zmenilo

Každá stavba dostáva **vlastnú sieť** vo vyhradenom rozsahu a na tom rozsahu
platí zákaz. Otvorené zostáva presne to, čo je na prácu potrebné: internet,
vlastná databáza stavby, vyhľadávanie v znalostnej báze a miestny jazykový
model. Všetko ostatné na tomto počítači je pre stavbu nedostupné.

Doteraz tri fázy z piatich bežali na spoločnej sieti, na ktorej sú aj cudzie
kontajnery — tá sa zakázať nedala. Teraz má každá stavba svoju.

## Dve veci, ktoré sa ukázali až pri meraní

**Pôvodný plán mieril na zlé miesto.** Mali sme zapísané jedno pravidlo; to by
zablokovalo prihlásenie na stroj, ale rozhranie NEX Studia **nie**. Služby,
ktoré zverejňuje Docker, totiž chodia inou cestou než služby samotného počítača.
Treba obe pravidlá — s jedným by to vyzeralo ochránené a nebolo by.

**Zverejnený port sa cestou prepisuje.** Výnimka pre vyhľadávanie a jazykový
model najprv nefungovala, lebo číslo, na ktoré sa pomocník pýta, nie je to,
ktoré dorazí na koniec. Pravidlo sa preto pýta na **pôvodné** číslo.

## Ochrana, ktorá sa sama vracia

Docker si pri každom reštarte prepíše sieťové nastavenia a zákaz by zmizol —
ticho. Preto ho každú minútu obnovuje samostatná služba. **Vyskúšané:** zákaz
sme zmazali, prihlásenie na stroj sa zo stavby otvorilo, a do minúty sa zavrelo
späť.

Appka pri štarte hovorí aj to, čo overiť **nevie** — z kontajnera do firewallu
hostiteľa nevidí. Radšej priznaná hranica než tichý predpoklad.
