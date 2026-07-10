## v3.0.139 — Overenie: spoľahlivejšie rozpoznanie záložky Aktualizácie

Kontrola pri vydaní falošne hlásila, že generovaná appka nemá v menu záložku **Aktualizácie**, hoci ju mala — len umiestnenú pod inou cestou (napr. `/admin/updates`) a zadefinovanú dátovo (zoznam položiek menu). Kontrola teraz rozpozná aj tieto bežné podoby navigácie, takže appku s platnou záložkou už falošne nezablokuje — a zároveň si udrží prísnosť voči nesúvisiacim cestám (napr. `/updates-log`).
