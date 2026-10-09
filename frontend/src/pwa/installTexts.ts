/**
 * All the button's sentences in one place.
 *
 * These are the texts approved in the specification, hence their own file instead of
 * being scattered across components. The app name is a PARAMETER, not a hard-coded
 * product name: otherwise the module could not move into `nex-shared`.
 */

import type { InstallGuide } from "./installGuides";
import type { InstallState } from "./installState";

export interface InstallDialogContent {
  title: string;
  /** Sentences above the guide. May be empty - then the guide speaks for itself. */
  lines: string[];
  /** Manual guide, when the dialog should carry one. */
  guide?: InstallGuide;
}

export interface TextOptions {
  appName: string;
  guide: InstallGuide;
  /** Did the person dismiss the browser offer in this window? Changes ONLY a sentence, not behaviour. */
  dismissed: boolean;
  /** The dialog opens right after a finished installation (confirmation). */
  justInstalled?: boolean;
}

/**
 * Real, common reasons why the browser did not offer installation (v1.2.1).
 *
 * The order is BINDING and this is not a list of options: it is ordered by how often
 * each reason really occurs. Restart stands first because it was the actual incident:
 * Chrome was waiting for a restart after its own update, and after closing and
 * reopening it installation worked on the first click. Before v1.2.0 none of this was
 * here - the dialog sent the person straight to the browser menu, where no install item
 * existed at that moment.
 */
function causes(dismissed: boolean): string[] {
  return [
    "Prehliadač sa práve aktualizoval a čaká na reštart. Zavri ho celý — nielen túto kartu — a otvor znova. Toto býva ten dôvod najčastejšie.",
    "Aplikácia už môže byť na tomto počítači nainštalovaná. Pozri sa medzi svoje programy — na Windowse do ponuky Štart, na Macu do Launchpadu.",
    dismissed
      ? "Ponuku si už raz zavrel, a prehliadač ju preto načas potlačil. Sama sa vráti neskôr."
      : "Ponuku mohol niekto raz zavrieť — vtedy ju prehliadač načas potlačí a sama sa vráti neskôr.",
  ];
}

/**
 * Introductory sentence of the manual path. It stands AFTER the causes and is
 * deliberately conditional: the browser adds the install item to its menu only when it
 * is willing to install. A step that cannot be performed is worse than admitting that
 * it does not work right now.
 */
const MANUAL_PATH_INTRO =
  "Ručná cesta cez ponuku prehliadača funguje len vtedy, keď prehliadač inštaláciu ponúka. Ak tú položku v ponuke vidíš, ide to takto:";

/**
 * Sentence in the bubble on mouse hover or keyboard focus.
 * The `ready` state has NO bubble - the button speaks for itself.
 *
 * Since v1.2.1 the `standalone` and `installed` branches never reach the screen: in
 * those states the button is not rendered and there is nothing for the bubble to
 * describe. They are deliberately kept in the `switch` - TypeScript uses it to
 * guarantee every state has a sentence; an incomplete `switch` would cancel that guard
 * for all the other states.
 */
export function bubbleFor(state: InstallState, opts: { dismissed: boolean }): string {
  switch (state) {
    case "preview":
      return "V živom náhľade sa aplikácia nainštalovať nedá — takto bude tlačidlo vyzerať v skutočnej aplikácii.";
    case "standalone":
      return "Aplikácia už beží vo vlastnom okne — inštalovať ju netreba.";
    case "ready":
      return "";
    case "installed":
      return "Aplikácia je na tomto počítači nainštalovaná — nájdeš ju medzi svojimi programami.";
    case "unsupported":
      return "Tento prehliadač inštaláciu sám neponúka. Klikni — ukážem ti, čo sa dá.";
    case "insecure":
      return "Stránka nebeží na zabezpečenej adrese (https), preto inštaláciu nemá ako ponúknuť žiadny prehliadač.";
    case "unavailable":
      return opts.dismissed
        ? "Ponuku na inštaláciu si zavrel. Klikni — ukážem ti, ako ju spustíš ručne."
        : "Prehliadač inštaláciu zatiaľ neponúkol. Klikni — ukážem ti, ako ju spustíš ručne.";
  }
}

/**
 * Content of the dialog that opens on click.
 *
 * It opens in EVERY state except `ready` - and in `ready` only when the offer has been
 * spent in the meantime. A click that does nothing is worse than no button.
 */
export function dialogFor(state: InstallState, opts: TextOptions): InstallDialogContent {
  const { appName, guide, dismissed, justInstalled } = opts;

  switch (state) {
    case "preview":
      return {
        title: "Živý náhľad",
        lines: [
          "V živom náhľade sa aplikácia nainštalovať nedá — beží bez zabezpečenej adresy a s predstieranými odpoveďami.",
          "V nasadenej aplikácii na adrese, ktorá začína https://, bude toto tlačidlo funkčné.",
        ],
      };

    case "standalone":
      return {
        title: "Aplikácia už beží vo vlastnom okne",
        lines: [
          "Inštalovať ju netreba — toto okno JE nainštalovaná aplikácia.",
          // Sentences must not depend on the GRAMMATICAL GENDER of the app name - the
          // module is meant to serve a whole family of apps, and a pronoun would sound
          // wrong with another name. Pronouns therefore point to the word "aplikácia",
          // not to the substituted name.
          `Keď ju chceš mať aj na inom počítači, otvor tam ${appName} v prehliadači a použi toto tlačidlo.`,
        ],
      };

    case "installed":
      return {
        title: justInstalled ? "Hotovo." : "Aplikácia je nainštalovaná",
        lines: [
          `${appName} nájdeš medzi svojimi programami — na Windowse aj v ponuke Štart, na Macu v Launchpade, na Linuxe v ponuke aplikácií.`,
          "Keby si aplikáciu medzitým odinštaloval, takto ju nainštaluješ znova:",
        ],
        guide,
      };

    case "insecure":
      return {
        title: "Stránka nebeží na zabezpečenej adrese",
        lines: [
          "Prehliadače ponúkajú inštaláciu len na adresách, ktoré začínajú https://.",
          `Otvor ${appName} na adrese, ktorá začína https:// (nie cez adresu s číslom portu), a tlačidlo ožije.`,
        ],
      };

    case "unsupported":
      return {
        title: guide.title,
        // For Firefox the guide itself speaks (including why it is impossible and what
        // to do instead).
        lines: guide.impossible
          ? []
          : ["Tento prehliadač inštaláciu sám neponúka, ale nainštalovať sa dá ručne:"],
        guide,
      };

    case "ready":
    case "unavailable":
      return {
        // NOT `guide.title`: a heading like "Installation in Chrome" promised a
        // procedure before the dialog had a chance to say the browser is not offering.
        title: dismissed
          ? "Ponuku na inštaláciu si zavrel"
          : "Prehliadač inštaláciu zatiaľ neponúkol",
        lines: guide.impossible
          ? []
          : [
              dismissed
                ? "Nie je to nadobro — a nemusí to byť ani jediný dôvod. Toto sú tri, ktoré za tým bývajú:"
                : "Neznamená to, že sa nedá. Skoro vždy je za tým jedna z týchto troch vecí:",
              ...causes(dismissed),
              MANUAL_PATH_INTRO,
            ],
        guide,
      };
  }
}
