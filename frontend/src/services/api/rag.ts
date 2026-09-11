import api from "../api";

/**
 * Sedí RAG index so Znalostnou bázou? (ICCINT-111)
 *
 * Čísla server POČÍTA naživo z disku a z indexu — nič sa neukladá, takže sa nemôžu rozísť s tým, čo
 * v indexe naozaj je. Keď je niektorá strana nedostupná, endpoint odpovie **503** a NIE nulou:
 * zelený údaj nad korpusom, o ktorom nevieme nič, by bol horší než pôvodná chyba.
 */
export interface KbIndexStatus {
  /** Koľko dokumentov Znalostnej bázy leží na disku (bez tajomstiev). */
  on_disk: number;
  /** Koľko dokumentov pozná index. */
  indexed: number;
  /** Súčet nezhôd — chýbajúce + zastarané + osirelé. Nula = sedí. */
  out_of_sync: number;
  /** Na disku sú, v indexe nie. */
  missing: number;
  /** V indexe sú, ale staršie než ich podoba na disku. */
  stale: number;
  /** V indexe zostali, hoci na disku už nie sú. */
  orphaned: number;
  /** Najnovší zápis do indexu naprieč korpusom (null = index je prázdny). */
  last_indexed_at: string | null;
  /** Prvých pár nesediacich dokumentov — aby číslo nebolo len číslo. */
  sample: string[];
}

export function getKbIndexStatus(): Promise<KbIndexStatus> {
  return api.get<KbIndexStatus>("/rag/index-status");
}
