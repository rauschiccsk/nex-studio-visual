# v4.78.0

**Nasadenie na server zákazníka už nestavia aplikáciu zakaždým od nuly.** Kokpit doteraz staval obrazy aplikácií
starým spôsobom stavby a po každom nasadení zmazal celú vyrovnávaciu pamäť — aj nainštalované knižnice, z ktorých by
ďalšia stavba vychádzala. NEX Inbox sa preto na serveri MÁGERSTAV staval zakaždým 12 až 27 minút. Teraz kokpit stavia
novším spôsobom (BuildKit) a po nasadení upratuje len pamäť stavby nad strop 8 GB, najdlhšie nepoužitú prvú. Ďalšie
nasadenie, ktoré knižnice nemení, ich už neinštaluje znova. Staršie verzie obrazov ako cesta späť ostávajú, ako boli.
