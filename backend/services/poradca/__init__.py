"""Poradca — agent, s ktorým sa ľudia v kokpite radia; len číta (ICCINT-167, ``docs/specs/poradca.md``).

Moduly:
  * :mod:`.secrets_filter` — tajomstvá sa nahradia „‹skryté›" skôr, než ich Poradca dostane, a ešte raz
    pred uložením odpovede;
  * :mod:`.sandbox` — dočasný kontajner jednej otázky: obmedzený režim Claude Code, projekt len na čítanie,
    súbory s tajomstvami prekryté prázdnymi;
  * :mod:`.mcp_server` + :mod:`.shim` — nástroje „zozadu" podáva backend cez unixový socket jednej otázky;
  * :mod:`.tools` — samotné nástroje;
  * :mod:`.runner` — beh otázky, priebeh, zastavenie, strop súbežnosti.
"""
