# v4.69.2

**Jednorazové kroky inštalácie už po skončení nehlásia „unhealthy“ ani v starších inštaláciách.** Migrácia databázy
(napríklad alembic-init alebo migrate) zdedila kontrolu zdravia z obrazu backendu a po úspešnom skončení ju Docker
ukazoval ako chorú. Nové inštalácie to mali opravené, staršie nie. Odteraz kokpit kontrolu jednorazovým krokom vypne
pri najbližšom nasadení aj v existujúcej inštalácii a v hlásení nasadenia to povie.
