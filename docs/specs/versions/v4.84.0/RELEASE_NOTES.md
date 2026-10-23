# v4.84.0

**Verzia dostane „Hotovo“, až keď dobehne kontrola na GitHube.** Keď Audítor verziu schváli, ale kontrola jej kódu na
GitHube ešte beží, kokpit už verziu nepustí „bez výsledku“. V Riadiacom centre napíše, že verzia čaká na kontrolu na
GitHube, a tlačidlo „Schváliť na Hotovo“ sa zatiaľ neukáže. Keď kontrola dobehne, kokpit to dorieši sám: pri zelenej
ponúkne schválenie (rýchla oprava sa dokončí sama), pri červenej vráti verziu na opravu. Čakanie prežije aj reštart
kokpitu a počas neho sa dá kokpit normálne nasadzovať.
