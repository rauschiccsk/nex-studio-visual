"""DEV-43 — Verifikácia finds every place a generated app decides "is this the preview?" outside the template's switch.

The cockpit starts the Vizuál preview with ``VITE_PREVIEW`` set. In the app only the template's helper
``isPreviewEnabled()`` may decide: it compares with the list of enabling values. The raw flag used as a truth value
lets ``VITE_PREVIEW=false`` through. Measured 09.10.2026 in three places — Career Asistent's API client (an expired
session would no longer go to the login), NEX Websites' route guard and its MSW start (a release would serve the
fixtures). The agent charter itself dictated the first shape (fixed in DEV-2); nothing in the cockpit looked at the
app's code. Director: „Áno, založ tiket do DEV“, „Poďme na ďaľší tiket.“

Pinned here: the rule (every use of the raw flag has an allowed SHAPE — static guard, comparison, handed-on
value), the three measured shapes are caught with file and line, the shapes the template, NEX Manager and NEX Inbox
use today pass, and the shared Verifikácia settle floors a PASS on it for the autonomous and the manual path alike.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import claude_agent, orchestrator, preview_switch
from tests.test_orchestrator_v2_verifikacia import _make_version, _msgs, _seed_done_tasks, _seed_verifikacia

# The three shapes measured on 09.10.2026, verbatim.
CAREER_API = """import { createApiClient } from "nex-shared";

const client = createApiClient({
  baseUrl: "",
  onUnauthorized: () => {
    // The preview has no backend session — never bounce it to the login wall (v4.0.45).
    if (import.meta.env.VITE_PREVIEW) return;
    window.location.assign("/login");
  },
});
"""
WEBSITES_GUARD = """export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  if (import.meta.env.VITE_PREVIEW) return <>{children}</>;
  return <Navigate to="/login" />;
}
"""
WEBSITES_MAIN = """async function bootstrap() {
  if (import.meta.env.VITE_PREVIEW) {
    const { worker } = await import("./mocks/browser");
    await worker.start({ onUnhandledRequest: "bypass" });
  }
}
"""

# The shapes in use today that are right, verbatim from the template, NEX Manager, NEX Inbox and Dedo Home.
TEMPLATE_HELPER = """/** Values that ENABLE the preview — and nothing else. */
const ENABLING_VALUES = ["1", "true", "yes", "on"];

export function isPreviewEnabled(
  value: string | undefined = import.meta.env.VITE_PREVIEW,
): boolean {
  return typeof value === "string" && ENABLING_VALUES.includes(value);
}
"""
TEMPLATE_MAIN = """import { isPreviewEnabled } from "./preview/isPreview";

// 1. `import.meta.env.VITE_PREVIEW` is a static guard, never `if (import.meta.env.VITE_PREVIEW)` alone.
/* if (import.meta.env.VITE_PREVIEW) { would be wrong } */
if (import.meta.env.VITE_PREVIEW && isPreviewEnabled()) {
  await startPreview();
}
if (
  import.meta.env.VITE_PREVIEW &&
  isPreviewEnabled()
) {
  render();
}
"""
INBOX_BROWSER = """/**
 * Vite substitutes `import.meta.env.VITE_PREVIEW` with a literal at build time.
 */
const PREVIEW_FLAG = import.meta.env.VITE_PREVIEW;

export const IS_PREVIEW: boolean =
  PREVIEW_FLAG === "1" ||
  PREVIEW_FLAG === "true" ||
  PREVIEW_FLAG === "yes" ||
  PREVIEW_FLAG === "on";
"""
DEDO_HOME_GUARD = """if (import.meta.env.VITE_PREVIEW && isPreviewEnabled(import.meta.env.VITE_PREVIEW)) return;
const on = isPreviewEnabled( import.meta.env.VITE_PREVIEW, ENABLING );
"""
OTHER_RIGHT_SHAPES = """const raw = import.meta.env.VITE_PREVIEW as string | undefined;
const orEmpty = import.meta.env.VITE_PREVIEW ?? "";
const typed = typeof import.meta.env.VITE_PREVIEW === "string";
const reversed = "1" === import.meta.env.VITE_PREVIEW;
const url = "https://example.com"; // if (import.meta.env.VITE_PREVIEW) in a comment after a URL
"""


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _where(found) -> list[tuple[str, int]]:
    return [(d.path, d.line) for d in found]


def test_the_three_measured_shapes_are_found_with_file_and_line(tmp_path):
    root = _project(
        tmp_path,
        {
            "frontend/src/services/api.ts": CAREER_API,
            "frontend/src/components/auth/ProtectedRoute.tsx": WEBSITES_GUARD,
            "frontend/src/main.tsx": WEBSITES_MAIN,
        },
    )

    found = preview_switch.find(root)

    assert _where(found) == [
        ("frontend/src/components/auth/ProtectedRoute.tsx", 2),
        ("frontend/src/main.tsx", 2),
        ("frontend/src/services/api.ts", 7),
    ]
    assert found[2].code == "if (import.meta.env.VITE_PREVIEW) return;"


def test_the_shapes_in_use_today_pass(tmp_path):
    root = _project(
        tmp_path,
        {
            "frontend/src/preview/isPreview.ts": TEMPLATE_HELPER,
            "frontend/src/main.tsx": TEMPLATE_MAIN,
            "frontend/src/mocks/browser.ts": INBOX_BROWSER,
            "frontend/src/config/flags.ts": OTHER_RIGHT_SHAPES,
            "frontend/src/services/api.ts": DEDO_HOME_GUARD,
        },
    )

    assert preview_switch.find(root) == []


@pytest.mark.parametrize(
    "line",
    [
        "if (!import.meta.env.VITE_PREVIEW) redirect();",
        "const page = import.meta.env.VITE_PREVIEW ? <Demo /> : <App />;",
        "const on = import.meta.env.VITE_PREVIEW || false;",
        "if (import.meta.env.VITE_PREVIEW && userIsGuest()) skipLogin();",
        "const on = Boolean(import.meta.env.VITE_PREVIEW);",
        "const on = !!import.meta.env.VITE_PREVIEW;",
        "if (isPreviewEnabled() && import.meta.env.VITE_PREVIEW) start();",
        'if (import.meta.env["VITE_PREVIEW"]) start();',
        "if (import.meta.env?.VITE_PREVIEW) start();",
        "const isPreview = () => import.meta.env.VITE_PREVIEW;",
        "track(import.meta.env.VITE_PREVIEW);",
        'const home = "https://isnex.eu"; if (import.meta.env.VITE_PREVIEW) start();',
    ],
)
def test_every_other_use_of_the_raw_flag_is_a_finding(tmp_path, line):
    root = _project(tmp_path, {"frontend/src/app.tsx": f"// header\n{line}\n"})

    assert _where(preview_switch.find(root)) == [("frontend/src/app.tsx", 2)]


def test_tests_are_not_the_app(tmp_path):
    truthy = "if (import.meta.env.VITE_PREVIEW) start();\n"
    root = _project(
        tmp_path,
        {
            "frontend/src/__tests__/preview.test.tsx": truthy,
            "frontend/src/main.test.ts": truthy,
            "frontend/src/api.spec.ts": truthy,
            "frontend/src/setupTests.ts": truthy,
            "frontend/src/tests/helpers.ts": truthy,
            "frontend/src/app.tsx": truthy,
        },
    )

    assert _where(preview_switch.find(root)) == [("frontend/src/app.tsx", 1)]


def test_an_unreadable_source_file_is_a_finding_not_a_pass(tmp_path, monkeypatch):
    root = _project(tmp_path, {"frontend/src/app.tsx": "export const x = 1;\n"})
    real_read_text = Path.read_text

    def _read_text(self, *args, **kwargs):
        if self.name == "app.tsx":
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)
    found = preview_switch.find(root)

    assert [(d.path, d.line, d.code) for d in found] == [
        ("frontend/src/app.tsx", 0, "súbor sa nedal prečítať (PermissionError)")
    ]


@pytest.mark.parametrize(
    ("count", "said"), [(1, "1 miesto"), (2, "2 miesta"), (4, "4 miesta"), (5, "5 miest"), (12, "12 miest")]
)
def test_the_manager_reads_the_count_in_slovak(count, said):
    assert preview_switch.places(count) == said


def test_a_project_without_a_frontend_has_nothing_to_find(tmp_path):
    assert preview_switch.find(tmp_path) == []


def test_the_fix_names_the_helper_and_every_allowed_shape():
    fix = preview_switch.fix_instruction()

    assert "`isPreviewEnabled()`" in fix and "`frontend/src/preview/isPreview.ts`" in fix
    assert "ak ho projekt nemá, vytvor ho v tvare šablóny" in fix
    assert "`import.meta.env.VITE_PREVIEW && isPreviewEnabled()`" in fix
    assert "aj `VITE_PREVIEW=false` je pravdivé" in fix


# ── the shared Verifikácia settle ────────────────────────────────────────────────────────────────────────────


def _record_pass(db_session, version_id):
    orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content="PASS",
        payload={"verdict": "PASS", "phase": "verifikacia"},
    )


def _app_with(project, files: dict[str, str]) -> None:
    _project(claude_agent.PROJECTS_ROOT / project.slug, files)


def _count_ci_questions(monkeypatch) -> list[int]:
    asked: list[int] = []

    async def _ci(root, **_kw):
        asked.append(1)
        return "green", "CI prešlo (beh 7, success)"

    monkeypatch.setattr(orchestrator, "_ci_status_for_head", _ci)
    return asked


async def test_a_pass_on_an_app_that_decides_outside_the_switch_is_floored_to_fail(db_session, monkeypatch):
    asked = _count_ci_questions(monkeypatch)
    version, project = _make_version(db_session, project_dial="plna")
    state = _seed_verifikacia(db_session, version.id, iteration=0)
    _seed_done_tasks(db_session, version, project, ["T1"])
    _app_with(project, {"frontend/src/services/api.ts": CAREER_API})
    _record_pass(db_session, version.id)

    settled = await orchestrator._settle_verifikacia_verdict(db_session, state, verdict="PASS")

    assert settled.status != "awaiting_manazer" and settled.current_stage == "programovanie"
    last = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"][-1]
    assert last.payload["verdict"] == "FAIL" and last.payload["engine_override"] == "preview_switch"
    assert last.content == (
        "Kód aplikácie rozhoduje o živom náhľade mimo prepínača zo šablóny (1 miesto) — aplikácia zostavená "
        "s vypnutým náhľadom by sa mohla správať ako náhľad. Opraví to AI Agent."
    )
    assert "frontend/src/services/api.ts:7 — `if (import.meta.env.VITE_PREVIEW) return;`" in last.payload["findings"]
    assert orchestrator._verifikacia_passed(db_session, version.id) is False
    assert asked == [], "a floored PASS has nothing for CI to confirm"

    scope = orchestrator._latest_verifikacia_fix_scope(db_session, version.id)
    assert "frontend/src/services/api.ts:7" in scope and preview_switch.fix_instruction() in scope


async def test_the_manual_pass_cannot_cross_it_either(db_session, monkeypatch):
    _count_ci_questions(monkeypatch)
    version, project = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_verifikacia(db_session, version.id)
    state.status = "awaiting_manazer"
    db_session.flush()
    _app_with(project, {"frontend/src/components/auth/ProtectedRoute.tsx": WEBSITES_GUARD})
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, st: None)

    new_state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="verdict", payload={"verdict": "PASS"}
    )

    assert new_state.status != "awaiting_manazer"
    last = [m for m in _msgs(db_session, version.id) if m.kind == "verdict"][-1]
    assert last.payload["engine_override"] == "preview_switch"
    assert orchestrator._verifikacia_passed(db_session, version.id) is False


async def test_an_app_that_uses_the_switch_passes_on_to_ci(db_session, monkeypatch):
    asked = _count_ci_questions(monkeypatch)
    version, project = _make_version(db_session, project_dial="plna")
    state = _seed_verifikacia(db_session, version.id, iteration=0)
    _app_with(project, {"frontend/src/preview/isPreview.ts": TEMPLATE_HELPER, "frontend/src/main.tsx": TEMPLATE_MAIN})
    _record_pass(db_session, version.id)

    settled = await orchestrator._settle_verifikacia_verdict(db_session, state, verdict="PASS")

    assert settled.status == "awaiting_manazer"
    assert asked == [1]
    assert orchestrator._verifikacia_passed(db_session, version.id) is True
