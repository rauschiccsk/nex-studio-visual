/**
 * Manual installation guides.
 *
 * PURE function: it takes the browser's user-agent string, not `navigator`, so tests
 * can cover it without faking the whole window. It never mentions the app by name, so
 * it can be moved into `nex-shared`.
 */

export interface InstallGuide {
  /** Dialog title. */
  title: string;
  /** Steps, each one a full sentence. */
  steps: string[];
  /** `true` = installing is NOT possible in this browser (desktop Firefox). */
  impossible?: boolean;
}

/**
 * Browsers move menu items around from time to time, and this cannot be verified here
 * (no browser runs in this environment). This sentence is a safety net: even when the
 * label does not match exactly, the person can find the way.
 *
 * Before v1.2.0 it ended with "look for Install or Add to home screen", which only
 * claims the item IS somewhere. In desktop Chrome it appears only when the browser
 * offers installation, so the person searched for an item that was not there at that
 * moment. The sentence therefore also admits that it may not be there at all.
 */
const FALLBACK_STEP =
  "Ak to v ponuke nevidíš, hľadaj položku Inštalovať alebo Pridať na plochu — prehliadače si ju občas presúvajú. Keď tam nie je ani tá, prehliadač inštaláciu práve neponúka a ručná cesta je zavretá.";

const GENERIC: InstallGuide = {
  title: "Inštalácia z ponuky prehliadača",
  steps: [
    "Otvor ponuku prehliadača.",
    "Hľadaj položku Inštalovať alebo Pridať na plochu.",
  ],
};

/**
 * The order of the conditions is BINDING: the strings contain each other, and a wrong
 * order does not crash, it puts a FALSEHOOD on screen. So every branch says why it
 * stands where it stands.
 */
export function guideFor(userAgent: string): InstallGuide {
  const ua = userAgent;

  // Android phones go FIRST, because of Firefox: on Android the app CAN be pinned
  // through it, although not on desktop. If the Firefox branch stood higher, the app
  // would tell a phone user "not possible" and send them to another browser for
  // nothing. Here the order does not save time, it guards against a falsehood.
  if (/Android/.test(ua)) {
    return withFallback({
      title: "Inštalácia na Androide",
      steps: [
        "Klepni na ⋮ vpravo hore.",
        "Vyber Pridať na plochu (alebo Inštalovať aplikáciu).",
      ],
    });
  }

  // On iPhone and iPad it is always Safari underneath, whatever the browser is called.
  if (/iPhone|iPad|iPod/.test(ua)) {
    return withFallback({
      title: "Inštalácia na iPhone alebo iPade",
      steps: [
        "Klepni na Zdieľať — štvorec so šípkou nahor.",
        "Vyber Pridať na plochu.",
      ],
    });
  }

  // MUST come before Chrome: Edge also identifies itself as Chrome in the string.
  if (/Edg\//.test(ua)) {
    return withFallback({
      title: "Inštalácia v prehliadači Edge",
      steps: [
        "Klikni na ⋯ vpravo hore.",
        "Vyber Aplikácie.",
        "Klikni na Inštalovať túto stránku ako aplikáciu.",
      ],
    });
  }

  // The only case where the answer is "not possible". Even so it is not bare: it offers
  // an alternative. That this is desktop Firefox is guaranteed solely by the order:
  // phones left earlier.
  if (/Firefox\//.test(ua)) {
    return {
      title: "Firefox na počítači inštaláciu nepodporuje",
      steps: [
        "Mozilla túto možnosť zo svojho prehliadača odstránila a obísť sa to nedá.",
        "Aplikácia vo Firefoxe funguje ďalej — len sa nedá pripnúť na plochu.",
        "Keď ju tam chceš mať, otvor ju v prehliadači Chrome alebo Edge.",
      ],
      impossible: true,
    };
  }

  // Only after Edge and Firefox: Edge also identifies as Chrome (above), and Chrome in
  // turn carries "Safari" in its string, which is why Safari stands after it.
  if (/Chrome|Chromium/.test(ua)) {
    return withFallback({
      title: "Inštalácia v prehliadači Chrome",
      steps: [
        "Klikni na ⋮ vpravo hore.",
        "Vyber Prenášať, uložiť a zdieľať.",
        // NOT "Click Install page as app." unconditionally: Chrome adds that item to the
        // menu ONLY when it is willing to install. As a certainty it promised a door
        // that is locked exactly when the person needs it (v1.2.1).
        "Ak tam vidíš položku Inštalovať stránku ako aplikáciu, klikni na ňu.",
      ],
    });
  }

  // Only what is neither Chrome nor Edge gets here, i.e. Safari on a Mac.
  if (/Safari\//.test(ua)) {
    return withFallback({
      title: "Inštalácia v Safari na Macu",
      steps: [
        "Klikni na ikonu Zdieľať v paneli s adresou.",
        "Vyber Pridať do Docku.",
      ],
    });
  }

  // Unknown browser: the generic guide. Never empty; a catch-all sentence would only
  // repeat the same thing.
  return GENERIC;
}

function withFallback(guide: InstallGuide): InstallGuide {
  return { ...guide, steps: [...guide.steps, FALLBACK_STEP] };
}
