// PrenosDoRiadnejVerzieBar — keď práca na rýchlu dráhu nepatrí, dá sa to aj vykonať (ICCINT-139).
//
// AI Agent na začiatku stavby NEX Inbox 1.5.2 napísal, že rozsah presahuje rýchlu opravu: tri zo štyroch
// bodov menia správanie zo Špecifikácie a dva žiadajú prestavbu údajov v ostrej prevádzke so 117 skutočnými
// faktúrami. Žiadal o potvrdenie. Manažér potvrdil. **Stavba aj tak dobehla ako rýchla oprava** — dráha sa
// nastaví raz, pri spustení, a žiadna akcia ju nemenila.
//
// 1.5.2 tak niesla migráciu databázy cez ĽAHKÚ kontrolu Audítora („oprava funguje + nič sa nerozbilo")
// namiesto plnej — a jeden zo štyroch bodov svoj účel nesplnil.
//
// ⚠️ Dráha sa NEPREPÍNA za behu: prepnúť ju uprostred by znamenalo domýšľať, ktoré už prebehnuté fázy ešte
// platia. Zakladá sa čistá riadna verzia z toho istého zadania a rýchla oprava sa pozastaví.
//
// Poctivé už z konštrukcie (ako ReverifyBar): panel sa NEVYKRESLÍ, kým backend tú akciu neponúka. Backend
// ju ponúka len na rýchlej dráhe a len na ustálenej stavbe — takže tlačidlo nikdy nesvieti tam, kde by
// nič neurobilo.

import { useState } from "react";
import { GitBranch } from "lucide-react";

import WarningActionBar from "@/components/common/WarningActionBar";
import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  onBoard: (board: PipelineBoard) => void;
}

export default function PrenosDoRiadnejVerzieBar({ board, versionId, onBoard }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  if (!board?.available_actions?.includes("na_riadnu_verziu")) return null;

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      onBoard(await postPipelineActionApi(versionId, { action: "na_riadnu_verziu" }));
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Prenos do riadnej verzie zlyhal"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <WarningActionBar
      variant="docked"
      title="Táto stavba beží ako rýchla oprava"
      action={{
        label: submitting ? "Prenášam…" : "Preniesť do riadnej verzie",
        icon: GitBranch,
        spinning: submitting,
        disabled: submitting,
        onClick: submit,
      }}
      error={error}
    >
      Rýchla oprava vynecháva fázu Návrhu a Audítor na nej robí len ľahkú kontrolu — overí, že oprava funguje
      a nič sa nerozbilo. Keď práca mení správanie aplikácie alebo siaha na údaje v ostrej prevádzke, patrí
      do riadnej verzie s plnou kontrolou. Tlačidlo založí novú verziu s tým istým zadaním a túto rýchlu
      opravu pozastaví — nič sa nestratí a nikde nevzniknú dve stavby na tej istej práci.
    </WarningActionBar>
  );
}
