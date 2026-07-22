# v4.0.29 — NEX Studio Visual

## Generované aplikácie majú čisté CI od začiatku

Predtým sa pri niektorých projektoch opakovane objavovalo „CI / lint Failed". Táto verzia to rieši systémovo:

- **CI funguje s hocijakým backendom** — či aplikácia používa poetry alebo moderný pip/PEP-621, CI si to sama rozpozná a nainštaluje správne. (Predtým natvrdo predpokladala poetry a na pip-projekte padla ešte pred spustením kontrol.)
- **AI Agent nemôže commitnúť nečistý kód** — každý nový projekt dostane pri založení **pred-commit bránu**, ktorá spustí presne tie isté kontroly ako CI (formátovanie + lint na backende, type-check na fronte). Commit, ktorý by CI zamietlo, sa **zablokuje lokálne** — nedostane sa na GitHub a nespustí červené CI.

Krátko: nový projekt má zelené CI hneď a ostáva zelené.
