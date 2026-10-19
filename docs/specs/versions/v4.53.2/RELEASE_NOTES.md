# v4.53.2

**Kontroly v oddelenom priestore stavajú obrazy ako predtým na serveri.** Oddelený priestor ukladal obrazy novším
spôsobom a pri projekte, ktorý stavia viac častí do jedného obrazu naraz, kontrola občas padla na hláške „already
exists“ (Career Asistent). Priestor teraz ukladá obrazy rovnako ako server, takže stavba sa nezrazí.
