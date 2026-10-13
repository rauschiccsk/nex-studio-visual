/**
 * DEV-86 — the editor of VS Code (Monaco) as NS Code uses it: viewing, never editing. Its own grammars highlight every
 * language it knows; the language services (TypeScript, JSON, CSS, HTML) are left out on purpose — in a read-only view
 * they would only underline imports they cannot resolve, and each brings a worker of several megabytes. Served from
 * the cockpit itself, never from a CDN, and loaded only when the NS Code tab opens.
 */

import * as monaco from "monaco-editor/editor/editor.api.js";
import "monaco-editor/languages/definitions/register.all.js";
// The icons of the find widget and the folding arrows. Stylesheets are not in the package's exports map, so they are
// imported by their place in the package.
import "../../../node_modules/monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon.css";
import "../../../node_modules/monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon-modifiers.css";
import "monaco-editor/editor/browser/coreCommands.js";
import "monaco-editor/editor/common/standaloneStrings.js";
import "monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching.js";
import "monaco-editor/editor/contrib/clipboard/browser/clipboard.js";
import "monaco-editor/editor/contrib/contextmenu/browser/contextmenu.js";
import "monaco-editor/editor/contrib/cursorUndo/browser/cursorUndo.js";
import "monaco-editor/features/find/register.js";
import "monaco-editor/editor/contrib/find/browser/findController.js";
import "monaco-editor/editor/contrib/folding/browser/folding.js";
import "monaco-editor/editor/contrib/fontZoom/browser/fontZoom.js";
import "monaco-editor/editor/standalone/browser/quickAccess/standaloneGotoLineQuickAccess.js";
import "monaco-editor/editor/contrib/lineSelection/browser/lineSelection.js";
import "monaco-editor/editor/contrib/longLinesHelper/browser/longLinesHelper.js";
import "monaco-editor/editor/contrib/multicursor/browser/multicursor.js";
import "monaco-editor/editor/contrib/readOnlyMessage/browser/contribution.js";
import "monaco-editor/editor/contrib/smartSelect/browser/smartSelect.js";
import "monaco-editor/editor/contrib/stickyScroll/browser/stickyScrollContribution.js";
import "monaco-editor/editor/contrib/tokenization/browser/tokenization.js";
import "monaco-editor/editor/contrib/unicodeHighlighter/browser/unicodeHighlighter.js";
import "monaco-editor/editor/contrib/wordHighlighter/browser/wordHighlighter.js";
import "monaco-editor/editor/contrib/wordOperations/browser/wordOperations.js";
// DEV-87 — the side-by-side view of changes, as VS Code compares a file.
import "monaco-editor/editor/browser/widget/diffEditor/diffEditor.contribution.js";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";

// The one worker the core editor needs (it computes e.g. the word occurrences off the page's thread).
self.MonacoEnvironment = { getWorker: () => new EditorWorker() };

export { monaco };
