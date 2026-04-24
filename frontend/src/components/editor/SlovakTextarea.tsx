import { useEffect, useMemo, useRef, useState } from "react";
import {
  addIgnoredWord,
  getIgnoredWords,
  getSpellchecker,
} from "@/services/spellchecker";
import SpellSuggestionMenu, {
  type SuggestionMenuState,
} from "./SpellSuggestionMenu";

/**
 * Drop-in replacement for ``<textarea>`` with a bundled Slovak
 * spellchecker — misspelled words get a red wavy underline and a
 * right-click menu with suggestions + "add to personal dictionary".
 *
 * Works browser-agnostically: the dictionary is shipped with the app
 * (see :mod:`services/spellchecker`) so users do not need to install
 * a browser Slovak dictionary.
 *
 * Implementation: a backdrop ``<div>`` is absolutely positioned
 * behind the textarea and mirrors its text with transparent glyphs
 * whose only visible decoration is the wavy underline on misspelled
 * spans. The backdrop's scroll is kept in sync with the textarea so
 * the underlines always line up with the visible text.
 *
 * The ``className`` prop is applied to the *wrapper* only — give it
 * borders, background, rounding, flex sizing. Font + padding +
 * resize handling are fixed inside the component so backdrop and
 * textarea always render identically.
 */

interface Props {
  value: string;
  onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  placeholder?: string;
  autoFocus?: boolean;
  className?: string;
  onFocus?: React.FocusEventHandler<HTMLTextAreaElement>;
  onBlur?: React.FocusEventHandler<HTMLTextAreaElement>;
}

interface MisspelledRange {
  start: number;
  end: number;
  word: string;
}

// Unicode word regex — letters + combining marks + digits + internal hyphens.
// We iterate separately so ``re.lastIndex`` is per-call deterministic.
const WORD_RE = /[\p{L}\p{M}][\p{L}\p{M}\p{N}]*(?:-[\p{L}\p{M}][\p{L}\p{M}\p{N}]*)*/gu;

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export default function SlovakTextarea({
  value,
  onChange,
  placeholder,
  autoFocus,
  className,
  onFocus,
  onBlur,
}: Props) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const bdRef = useRef<HTMLDivElement>(null);
  const [misspelled, setMisspelled] = useState<MisspelledRange[]>([]);
  const [menu, setMenu] = useState<SuggestionMenuState | null>(null);
  // Bumped by "Ignore word" so the debounced effect re-runs even when
  // ``value`` is unchanged.
  const [ignoreTick, setIgnoreTick] = useState(0);

  // ─── Spellcheck (debounced) ──────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(async () => {
      const spell = await getSpellchecker();
      if (cancelled) return;
      const ignored = getIgnoredWords();
      const text = value;
      const matches: MisspelledRange[] = [];
      let m: RegExpExecArray | null;
      WORD_RE.lastIndex = 0;
      while ((m = WORD_RE.exec(text)) !== null) {
        const word = m[0];
        if (word.length < 2) continue;
        if (ignored.has(word.toLowerCase())) continue;
        if (!spell.correct(word)) {
          matches.push({ start: m.index, end: m.index + word.length, word });
        }
      }
      if (!cancelled) setMisspelled(matches);
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [value, ignoreTick]);

  // ─── Scroll sync ─────────────────────────────────────────────────────────
  const syncScroll = () => {
    const ta = taRef.current;
    const bd = bdRef.current;
    if (!ta || !bd) return;
    bd.scrollTop = ta.scrollTop;
    bd.scrollLeft = ta.scrollLeft;
  };

  // ─── Backdrop HTML ───────────────────────────────────────────────────────
  const backdropHtml = useMemo(() => {
    if (misspelled.length === 0) return escapeHtml(value) + "​";
    let out = "";
    let pos = 0;
    for (let i = 0; i < misspelled.length; i++) {
      const m = misspelled[i]!;
      out += escapeHtml(value.slice(pos, m.start));
      out += `<span data-idx="${i}" class="nex-misspelled">${escapeHtml(m.word)}</span>`;
      pos = m.end;
    }
    out += escapeHtml(value.slice(pos));
    // Trailing zero-width char so a final newline is rendered with height,
    // matching the textarea's caret-on-empty-line layout.
    out += "​";
    return out;
  }, [value, misspelled]);

  // ─── Right-click handling ────────────────────────────────────────────────
  const handleContextMenu = async (e: React.MouseEvent<HTMLTextAreaElement>) => {
    // Look for a backdrop ``.nex-misspelled`` span underneath the cursor
    // via ``elementsFromPoint``, which ignores pointer-events:none but
    // still reports layout hits.
    const els = document.elementsFromPoint(e.clientX, e.clientY);
    const span = els.find(
      (el) => el instanceof HTMLElement && el.classList.contains("nex-misspelled"),
    ) as HTMLElement | undefined;
    if (!span) return;
    const idxStr = span.dataset.idx;
    if (idxStr === undefined) return;
    const idx = Number(idxStr);
    const hit = misspelled[idx];
    if (!hit) return;

    e.preventDefault();
    const spell = await getSpellchecker();
    const suggestions = spell.suggest(hit.word).slice(0, 6);
    setMenu({
      word: hit.word,
      suggestions,
      x: e.clientX,
      y: e.clientY,
      start: hit.start,
      end: hit.end,
    });
  };

  // ─── Replace / Ignore actions ────────────────────────────────────────────
  const emitChange = (newValue: string) => {
    const ta = taRef.current;
    if (!ta) return;
    // Use the native setter so React's synthetic event propagates normally.
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      "value",
    )?.set;
    setter?.call(ta, newValue);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  };

  const handleReplace = (replacement: string) => {
    if (!menu) return;
    const before = value.slice(0, menu.start);
    const after = value.slice(menu.end);
    emitChange(before + replacement + after);
    setMenu(null);
  };

  const handleIgnore = () => {
    if (!menu) return;
    addIgnoredWord(menu.word);
    setIgnoreTick((t) => t + 1);
    setMenu(null);
  };

  return (
    <div className={`relative ${className ?? ""}`}>
      <div
        ref={bdRef}
        aria-hidden
        className="absolute inset-0 overflow-hidden pointer-events-none whitespace-pre-wrap break-words px-4 py-3 text-sm font-mono leading-relaxed text-transparent"
        style={{ color: "transparent" }}
        dangerouslySetInnerHTML={{ __html: backdropHtml }}
      />
      <textarea
        ref={taRef}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        autoFocus={autoFocus}
        onFocus={onFocus}
        onBlur={onBlur}
        onScroll={syncScroll}
        onContextMenu={handleContextMenu}
        spellCheck={false}
        lang="sk"
        className="relative w-full h-full bg-transparent px-4 py-3 text-sm text-slate-200 font-mono leading-relaxed resize-none focus:outline-none"
      />
      {menu && (
        <SpellSuggestionMenu
          menu={menu}
          onReplace={handleReplace}
          onIgnore={handleIgnore}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  );
}
