# v4.91.0

**Pravidlá rolí z prvej verzie kokpitu odchádzajú z projektov do archívu.** NEX Manager a NEX Inbox ešte niesli
pravidlá rolí designer, implementer, coordinator a customer z čias, keď kokpit pracoval s viacerými agentmi. Dnes
ich nič nepoužíva, no ležali vedľa platných pravidiel a mohli agenta mýliť. Kokpit ich pri obnove pravidiel pred
sedením agenta presunie do priečinka `.claude/agents-v1-archiv/` v projekte a pripíše poznámku, prečo tam sú.
Nič sa nezmaže a do gitu projektu sa archív nedostane; v `.claude/agents/` ostanú len roly AI Agenta a Audítora.
