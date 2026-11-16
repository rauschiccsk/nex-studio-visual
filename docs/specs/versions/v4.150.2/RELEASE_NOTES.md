# v4.150.2

**Adresa databázy v nastaveniach inštalácie vedie na databázu tej inštalácie.** Staršie inštalácie si ponechávajú
vlastné mená služieb — databáza NEX Inboxu u MÁGERSTAVu sa volá „postgres“, v novších verziách projektu „db“. Kokpit
dopĺňal adresu databázy podľa projektu, takže skúška nasadenia pri inštalácii MÁGERSTAVu nenašla databázu a nasadenie
novej verzie by do nastavení doplnilo adresu, ktorá v inštalácii neexistuje. Teraz adresa vždy vedie na databázovú
službu inštalácie, pri skutočnom nasadení aj v skúške.

**Keď skúška nasadenia vo Verifikácii zlyhá, nález obsahuje aj záznam služby, ktorá skončila chybou** (napríklad
krok, ktorý zakladá tabuľky v databáze), a pri backende, ktorý neodpovedá, jeho záznam. Staviteľ tak vidí skutočnú
príčinu a nemusí ju hádať.
