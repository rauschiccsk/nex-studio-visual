# v4.101.1

**Kontrola spustenia aplikácie už nepadá na zvyškoch starej skúšky.** Pomocná služba, ktorú skúška vydania spúšťa
len na chvíľu (napríklad náhradný NEX Manager), po sebe ostávala ležať a každá ďalšia kontrola ju hlásila ako
zlyhanú. Kokpit teraz po každej skúške uprace všetko a pred kontrolou odstráni aj to, čo zostalo z predošlého behu.
