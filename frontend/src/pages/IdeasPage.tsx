// Nápady (DEV-318, docs/specs/ideas.md) — a new idea talked over with Poradca before any project exists.
//
// Director 06.11.2026: „Návrhy nových projektov sa mi zhromažďujú. Navrhni mi riešenie, aby som mohol tie návrhy robiť
// pokiaľ ty pracuješ nad kokpitom.“ — „Robiť nový projekt pod iným cudzím projektom, je nelogické.“ So this screen
// needs no pinned project. On the left my ideas (the admin account sees all) with where each stands; on the right the
// talk with Poradca — the same answers, images and live progress as in a project's Poradca — and, under the latest
// brief, the two ways it ends: a new project, or the brief added to an existing one. The address carries the idea
// (`/napady/<id>`); bare `/napady` starts a new one. Director 06.11.2026 on the name: „Súhlasím, nazveme to Nápady“.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Lightbulb } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import IdeaOutcomeActions from "@/components/poradca/IdeaOutcomeActions";
import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";
import PoradcaComposer, {
  type IncomingQuestion,
} from "@/components/poradca/PoradcaComposer";
import PoradcaConversationItem, {
  type Removal,
} from "@/components/poradca/PoradcaConversationItem";
import QuestionImages from "@/components/poradca/QuestionImages";
import { draftKey } from "@/hooks/useDraft";
import { useFollowBottom } from "@/hooks/useFollowBottom";
import { usePoradcaConversation } from "@/hooks/usePoradcaConversation";
import { followUpStart, followUpTitle, ideaStateLabel } from "@/lib/ideas";
import { formatCost } from "@/lib/poradcaAnswer";
import { toUpload } from "@/lib/poradcaImages";
import {
  createIdeaApi,
  getIdeaApi,
  listIdeasApi,
  throwAwayIdeaApi,
} from "@/services/api/ideas";
import {
  askPoradcaApi,
  getPoradcaStatusApi,
  renamePoradcaConversationApi,
} from "@/services/api/poradca";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { useActiveContextStore } from "@/store/activeContextStore";
import type { Idea, PoradcaStatus } from "@/types/poradca";
import { formatDate } from "@/utils/format";

const NEW_IDEA_DRAFT = "nove";

/** DEV-331 — an idea that ended in a project leaves only the list; its talk stays with the project (Director 09.11.2026). */
function ideaRemoval(idea: Idea): Removal | undefined {
  if (idea.state !== "founded" && idea.state !== "added") return undefined;
  const where = idea.outcome_project_name
    ? `projektu ${idea.outcome_project_name}`
    : "projektu";
  return {
    question: `Odstrániť „${idea.title}“ zo zoznamu Nápadov?`,
    note: idea.talk_in_project
      ? `Rozhovor ostane v Poradcovi ${where}.`
      : `Požiadavka ostane v Zásobníku ${where}.`,
    action: "Odstrániť",
  };
}

export default function IdeasPage() {
  const { ideaId } = useParams<{ ideaId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  // DEV-350: a new idea opened from a founded one starts with what the cockpit knows — read once, then wiped.
  const [incoming, setIncoming] = useState<IncomingQuestion | null>(null);
  useEffect(() => {
    const followUp = (location.state as { followUp?: string } | null)?.followUp;
    if (ideaId || !followUp) return;
    setIncoming({ question: followUp, held: null, fill: true });
    window.history.replaceState({}, "");
  }, [ideaId, location.state]);
  const setSelectedProject = useActiveContextStore((s) => s.setSelectedProject);
  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [current, setCurrent] = useState<Idea | null>(null);
  const [status, setStatus] = useState<PoradcaStatus | null>(null);
  const [loadError, setLoadError] = useState<HumanError | null>(null);
  // On a phone the list and the talk take turns, as in Poradca; from md up they stand side by side.
  const [pane, setPane] = useState<"list" | "talk">("talk");
  const conversationId = current?.conversation_id ?? null;
  const {
    detail,
    error: convError,
    setDetail,
  } = usePoradcaConversation(conversationId);

  const refreshList = useCallback(async () => {
    try {
      setIdeas(await listIdeasApi());
    } catch (e: unknown) {
      setLoadError(humanizeApiError(e, "Nápady sa nepodarilo načítať"));
    }
  }, []);

  const refreshCurrent = useCallback(async () => {
    if (!ideaId) {
      setCurrent(null);
      return;
    }
    try {
      setCurrent(await getIdeaApi(ideaId));
    } catch (e: unknown) {
      setCurrent(null);
      setLoadError(humanizeApiError(e, "Nápad sa nepodarilo načítať"));
    }
  }, [ideaId]);

  useEffect(() => {
    void refreshList();
    getPoradcaStatusApi()
      .then(setStatus)
      .catch((e: unknown) =>
        setLoadError(
          humanizeApiError(e, "Stav Projektanta sa nepodarilo zistiť"),
        ),
      );
  }, [refreshList]);

  useEffect(() => {
    setLoadError(null);
    void refreshCurrent();
  }, [refreshCurrent]);

  // A finished answer may have brought a brief: where the idea stands is read again with the conversation.
  const lastMessage = detail?.messages?.[detail.messages.length - 1];
  const finishedAnswers = (detail?.messages ?? []).filter(
    (m) => m.author === "poradca" && m.status !== "running",
  ).length;
  useEffect(() => {
    if (finishedAnswers === 0) return;
    void refreshCurrent();
    void refreshList();
  }, [finishedAnswers, refreshCurrent, refreshList]);

  const thread = useFollowBottom<HTMLDivElement>(
    `${detail?.messages?.length ?? 0}:${lastMessage?.steps?.length ?? 0}:${lastMessage?.status ?? ""}`,
    conversationId,
  );
  const running = !!detail?.messages?.some((m) => m.status === "running");
  const notReady =
    status && !status.ready ? (status.problems ?? []).join("; ") : null;
  const concluded = current?.state === "founded" || current?.state === "added";
  const latestBrief = useMemo(
    () =>
      [...(detail?.messages ?? [])]
        .reverse()
        .find(
          (m) =>
            m.author === "poradca" &&
            m.status === "done" &&
            (m.idea_project || m.idea_addition),
        ) ?? null,
    [detail],
  );

  function openProjectPoradca() {
    if (!current?.outcome_project_slug) return;
    setSelectedProject({
      slug: current.outcome_project_slug,
      name: current.outcome_project_name ?? current.outcome_project_slug,
    });
    navigate(
      `/poradca?${new URLSearchParams({ c: current.conversation_id }).toString()}`,
    );
  }

  // DEV-331 — renamed and deleted in its row exactly as a Poradca conversation (Director 09.11.2026).
  async function rename(idea: Idea, title: string): Promise<boolean> {
    try {
      await renamePoradcaConversationApi(idea.conversation_id, title);
      await refreshList();
      if (idea.id === ideaId) void refreshCurrent();
      return true;
    } catch (e: unknown) {
      setLoadError(humanizeApiError(e, "Nápad sa nepodarilo premenovať"));
      return false;
    }
  }

  async function remove(idea: Idea): Promise<boolean> {
    try {
      await throwAwayIdeaApi(idea.id);
      if (idea.id === ideaId) navigate("/napady");
      await refreshList();
      return true;
    } catch (e: unknown) {
      setLoadError(humanizeApiError(e, "Nápad sa nepodarilo vymazať"));
      return false;
    }
  }

  function open(path: string) {
    navigate(path);
    setPane("talk");
  }

  return (
    <div className="grid h-full grid-cols-1 grid-rows-[auto_minmax(0,1fr)] bg-[var(--color-canvas)] md:grid-cols-[260px_minmax(0,1fr)] md:grid-rows-1">
      <div
        role="tablist"
        aria-label="Čo ukázať"
        className="row-start-1 flex border-b border-[var(--color-border-default)] bg-[var(--color-surface)] md:hidden"
      >
        {(
          [
            ["list", "Nápady"],
            ["talk", "Rozhovor"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={pane === id}
            onClick={() => setPane(id)}
            className={`flex-1 py-2 text-xs font-medium ${
              pane === id
                ? "border-b-2 border-[var(--color-accent-primary)] text-[var(--color-text-primary)]"
                : "text-[var(--color-text-muted)]"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <aside
        className={`row-start-2 flex min-h-0 flex-col border-r border-[var(--color-border-default)] bg-[var(--color-surface)] md:col-start-1 md:row-start-1 ${
          pane === "list" ? "" : "max-md:hidden"
        }`}
      >
        <div className="flex items-center justify-between px-3 py-3">
          <h1 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Nápady
          </h1>
          <button
            type="button"
            onClick={() => open("/napady")}
            className="flex items-center gap-1 rounded-lg bg-primary-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
          >
            <Lightbulb className="h-3.5 w-3.5" /> Nový nápad
          </button>
        </div>
        <ul
          aria-label="Moje nápady"
          className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-3"
        >
          {ideas.length === 0 && (
            <li className="px-2 text-xs text-[var(--color-text-muted)]">
              Zatiaľ žiadny nápad.
            </li>
          )}
          {ideas.map((i) => (
            <PoradcaConversationItem
              key={i.id}
              noun="nápad"
              conversation={{
                title: i.title,
                running: i.running,
                version_number: null,
                updated_at: i.updated_at,
              }}
              active={i.id === ideaId}
              busy={i.running}
              authorName={null}
              subtitle={`${ideaStateLabel(i)} · ${formatDate(i.updated_at)}${i.cost != null ? ` · ${formatCost(i.cost)}` : ""}`}
              removal={ideaRemoval(i)}
              onSelect={() => open(`/napady/${i.id}`)}
              onRename={(title) => rename(i, title)}
              onDelete={() => remove(i)}
            />
          ))}
        </ul>
      </aside>

      <section
        className={`row-start-2 flex min-h-0 min-w-0 flex-col md:col-start-2 md:row-start-1 ${
          pane === "talk" ? "" : "max-md:hidden"
        }`}
      >
        {current && (
          <header className="flex flex-shrink-0 flex-wrap items-center gap-3 border-b border-[var(--color-border-default)] px-4 py-2">
            <div className="min-w-0 flex-1">
              <h2 className="truncate text-sm font-semibold text-[var(--color-text-primary)]">
                {current.title}
              </h2>
              <p className="text-[11px] text-[var(--color-text-muted)]">
                {ideaStateLabel(current)} · {current.author_name}
              </p>
            </div>
          </header>
        )}
        {current && concluded && (
          <div
            role="status"
            className="flex flex-shrink-0 flex-wrap items-center gap-3 border-b border-[var(--color-border-default)] bg-[var(--color-state-success-bg)] px-4 py-2 text-xs text-[var(--color-state-success-fg)]"
          >
            <span>
              {current.state === "founded"
                ? `Z nápadu vznikol projekt ${current.outcome_project_name}; zadanie je vo verzii 0.1.0.`
                : `Zadanie je v Zásobníku projektu ${current.outcome_project_name} ako REQ-${current.outcome_backlog_number}.`}
              {current.plane_identifier
                ? ` Tiket v DEV: ${current.plane_identifier}.`
                : ""}{" "}
              {current.talk_in_project
                ? "Rozhovor pokračuje v Poradcovi projektu."
                : "Projekt vedie iný manažér — rozhovor ostáva tu v Nápadoch."}
            </span>
            {current.talk_in_project && (
              <>
                <button
                  type="button"
                  onClick={() =>
                    navigate(`/projects/${current.outcome_project_slug}`)
                  }
                  className="underline"
                >
                  Otvoriť projekt
                </button>
                <button
                  type="button"
                  onClick={openProjectPoradca}
                  className="underline"
                >
                  Pokračovať v Poradcovi projektu
                </button>
              </>
            )}
            {current.state === "founded" && (
              <button
                type="button"
                onClick={() =>
                  navigate("/napady", {
                    state: { followUp: followUpStart(current) },
                  })
                }
                className="underline"
              >
                Nový nápad nadväzujúci na tento projekt
              </button>
            )}
          </div>
        )}

        <div
          ref={thread.ref}
          onScroll={thread.onScroll}
          role="log"
          aria-label="Rozhovor s Projektantom o nápade"
          className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3"
        >
          <ErrorNote error={loadError} />
          {convError && (
            <p className="text-xs text-[var(--color-state-error-fg)]">
              {convError}
            </p>
          )}
          {!ideaId && (
            <div className="max-w-xl space-y-1 text-xs text-[var(--color-text-muted)]">
              <p>
                Vlož svoj nápad na novú aplikáciu — pokojne celý text z
                Obsidianu, aj s náčrtmi obrazoviek. Projektant pozrie, čo už
                máme, opýta sa ťa po jednej otázke a nakoniec napíše zadanie
                prvej verzie.
              </p>
              <p>
                Z hotového zadania založíš tlačidlom nový projekt, alebo ho
                pridáš do existujúceho projektu, keď sa ukáže, že tam patrí.
                Projektant sám nič nezaloží a nič nezmení.
              </p>
            </div>
          )}
          {(detail?.messages ?? []).map((m) =>
            m.author === "human" ? (
              <div
                key={m.id}
                className="ml-auto max-w-[80%] whitespace-pre-wrap rounded-lg border border-[var(--color-accent-primary)]/30 bg-[var(--color-accent-primary)]/10 px-3 py-2 text-sm text-[var(--color-text-primary)]"
              >
                {m.content}
                {conversationId && (m.attachments ?? []).length > 0 && (
                  <QuestionImages
                    conversationId={conversationId}
                    attachments={m.attachments ?? []}
                  />
                )}
              </div>
            ) : (
              <div key={m.id} className="space-y-2">
                <PoradcaAnswer
                  message={m}
                  scopeVersion={null}
                  projectVersions={[]}
                  agentName="Projektant"
                />
                {current && !concluded && latestBrief?.id === m.id && (
                  <IdeaOutcomeActions
                    idea={current}
                    message={m}
                    onEnded={(outcome) => {
                      setCurrent((prev) =>
                        prev ? { ...prev, ...outcome } : prev,
                      );
                      void refreshList();
                    }}
                  />
                )}
              </div>
            ),
          )}
        </div>

        <PoradcaComposer
          draftKey={draftKey("ideas", ideaId ?? NEW_IDEA_DRAFT)}
          disabledReason={
            concluded
              ? current?.talk_in_project
                ? "Nápad skončil v projekte — rozhovor pokračuje v Poradcovi projektu."
                : "Nápad skončil — požiadavka je v Zásobníku projektu, ktorý vedie iný manažér."
              : notReady
                ? `Projektant teraz nevie bežať: ${notReady}`
                : running
                  ? "Projektant odpovedá — ďalšiu správu pošleš, keď dokončí."
                  : null
          }
          limits={status}
          incoming={ideaId ? null : incoming}
          placeholder={ideaId ? "Odpovedz Projektantovi…" : "Opíš svoj nápad…"}
          onAsk={async (question, images) => {
            thread.follow();
            const uploads = await Promise.all(images.map(toUpload));
            if (conversationId) {
              setDetail(await askPoradcaApi(conversationId, question, uploads));
            } else {
              // DEV-350: a follow-up idea is named by what the person wants, not by the cockpit's first sentence.
              const title = followUpTitle(question);
              const created = title
                ? await createIdeaApi(question, uploads, title)
                : await createIdeaApi(question, uploads);
              navigate(`/napady/${created.id}`);
            }
            void refreshList();
          }}
        />
      </section>
    </div>
  );
}
