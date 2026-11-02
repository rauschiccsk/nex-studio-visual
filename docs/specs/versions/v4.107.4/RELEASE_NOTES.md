# v4.107.4

**Na šablónu aplikácie sa dá previesť aj projekt, ktorého server spravuje balíky cez Poetry.** Prevod ho už neodmietne:
pridá manifest a balíky frontendu ako doteraz a server nechá nedotknutý. Položka Prípravy „Prechod na šablónu
aplikácie“ agentovi povie, aby šablónu doplnil do balíkov servera tým istým nástrojom aj so zámkom presných verzií
(poetry lock). Prvý taký projekt je NEX Manager.
