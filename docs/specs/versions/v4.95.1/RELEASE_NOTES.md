# v4.95.1

**Obnovený vykonávač kontrol hlási pripravenosť, až keď naozaj prijíma úlohy.** Po spustení alebo obnove vykonávača
kokpit počká, kým vykonávač dostane na GitHube vlastnú reláciu („Listening for Jobs“ v jeho zázname). GitHub totiž
ešte chvíľu po obnove hlási „online“ aj vtedy, keď reláciu drží nahradený vykonávač, a kontrola spustená v tej chvíli
mohla zostať visieť vo fronte (26.10. pri NEX Websites 14 minút). Keď vykonávač reláciu do piatich minút nedostane,
kokpit to povie jeho menom namiesto hlásenia „prevedený“.
