# v4.87.0

**Agent dostane obsah tiketu, na ktorý sa odkazuje, jedným klikom z Plane.** Keď zadanie verzie spomína tiket bez
jeho obsahu (napríklad návrh v tikete MAGER-66), agent sa naň spýta a tiket pomenuje číslom. Manažér klikne „Overiť
s Poradcom“; Poradca tiket prečíta podľa čísla aj s komentármi a pod odpoveďou ponúkne tlačidlo „Priložiť k odpovedi
agentovi“. Kokpit vezme tiket priamo z Plane, uloží ho agentovi ako súbor mimo gitu a do odpovede doplní, kde leží —
odpoveď odošle Manažér sám. Kokpit prečíta tikety len z evidencií, ktorých je jeho účet v Plane členom; pri inej
evidencii to povie presne.
