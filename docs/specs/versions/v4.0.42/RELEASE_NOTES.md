# v4.0.42 — NEX Studio Visual

## AI Agent sa konečne spustí

Predošlá verzia pridala AI Agenta, no ten sa ešte nedal spustiť z dvoch dôvodov — oba opravené:

- **Agent na správnej ceste** — systém spúšťa agenta príkazom `claude`, ktorý však nebol na štandardnej ceste; teraz je.
- **Beh v izolovanom prostredí** — serverové prostredie beží ako správca (root) a agent potrebuje pracovať samostatne bez potvrdzovaní; prostredie je teraz označené ako izolované (sandbox), takže to agent zvládne.

Overené naživo: agent v serverovom prostredí reálne odpovedal. Používateľ teraz spustí tvorbu špecifikácie → agent nabehne a začne pracovať.
