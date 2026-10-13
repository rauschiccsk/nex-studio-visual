# v4.47.0

**Tvoja poznámka k oprave po Verifikácii už nevytlačí nálezy Auditora.** Keď si na karte Verifikácie pripísal
k oprave vlastný pokyn, AI Agent dostal len ten pokyn — nálezy Auditora a navrhnutú opravu nie. Pri NEX Inbox 1.8.0
tak pokyn „body 3(a) a 3(b) sú povinné“ odkazoval na body, ktoré agent nemal, a stavba sa zastavila s otázkou.
Teraz agent dostane oboje: najprv nálezy Auditora s navrhnutou opravou, pod nimi tvoj pokyn, ktorý má prednosť, kde
sa rozchádzajú. Karta to pri „Usmerniť opravu“ aj takto opisuje.

**„Zatiaľ podržať“ na karte Verifikácie naozaj podrží.** Keď bol v poli pokynu nejaký text — napríklad pokyn od
Poradcu alebo obnovený rozpísaný text — voľba „Zatiaľ podržať“ ho poslala ako pokyn na opravu a stavba prešla do
programovania. A aj bez textu karta po podržaní z obrazovky zmizla, takže nebolo čo stlačiť. Teraz stavba počká na
karte, karta ostane na obrazovke s vetou „Podržané — karta počká na tvoje rozhodnutie.“ a text v poli sa nepošle —
ostane tam na neskôr. To isté platí pre kartu po vyčerpaných kolách Verifikácie.
