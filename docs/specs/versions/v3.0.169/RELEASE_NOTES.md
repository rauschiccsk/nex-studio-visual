## v3.0.169 — UAT nasadenie zákazníka je per-projekt (zanorené, ako PROD)

Nasadenie UAT pre zákazníka cez NEX Studio doteraz vyrábalo ploché, len-podľa-zákazníka meno (`uat-andros-uat`) — nehovorilo, o ktorý projekt ide, a projekty toho istého zákazníka by si prekážali. Odteraz UAT pristáva presne ako PROD: **zanorené `/opt/uat/<zákazník>/<projekt>`** s názvom **`uat-<zákazník>-<projekt>`** (napr. `uat-andros-payables`). Každý projekt zákazníka má vlastný, jasne pomenovaný UAT. Projektové UAT (spoločné, cez samostatný nástroj) ostávajú nezmenené.
