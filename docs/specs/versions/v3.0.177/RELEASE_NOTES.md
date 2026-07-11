## v3.0.177 — Nasadenie ponúka najnovšiu verziu (nie starú)

V prehľade nasadení (UAT aj PROD) sa v stĺpci „Nasadiť verziu" niekedy predvyplnila **staršia** verzia (napr. 1.0.0), hoci nasadená bola novšia (1.1.0) — hrozilo, že niekto omylom nasadí starú verziu. Príčinou bolo nesprávne triedenie verzií (miešal sa tvar `v1.0.0` a `1.1.0`). Odteraz sa verzie triedia **správne podľa čísla** a predvyplnená je vždy **najnovšia overená verzia**. Staršiu verziu je stále možné vybrať zámerne (napr. návrat späť), ale nezvolí sa náhodou.
