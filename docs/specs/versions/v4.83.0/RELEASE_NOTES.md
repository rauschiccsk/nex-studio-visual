# v4.83.0

**Pravidlá AI Agenta už nie sú súčasťou kódu aplikácie.** Kokpit zapisuje agentovi do projektu jeho pravidlá a
zručnosti pred každým sedením — doteraz však ležali aj v gite aplikácie, pri každom sedení sa menili a zákazník by ich
dostal spolu so zdrojovým kódom. Po najbližšej hotovej úlohe ich kokpit z gitu projektu vyberie jedným vlastným
commitom (na disku ostanú, agent ich číta ďalej) a odteraz sa tam už nedostanú. Pracovný priečinok projektu tak
neukazuje zmeny, ktoré nikto neurobil.
