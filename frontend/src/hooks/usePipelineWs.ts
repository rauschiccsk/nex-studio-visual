// Live pipeline board subscription (F-007 §7, CR-NS-018 Phase 4).
//
// Fetches the board once over REST, then keeps it live via the cockpit WS.
// The open WS connection doubles as the §9 Director-presence signal — a live
// connection anywhere in NEX Studio means "Director is in-app".

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../services/api";
import { useAuthStore } from "../store/authStore";
import { usePresenceStore } from "../store/usePresenceStore";
import {
  buildPipelineWsUrl,
  getPipelineBoardApi,
  type ActivityLine,
  type HelpersFeed,
  type PipelineBoard,
  type PipelineWsFrame,
} from "../services/api/pipeline";
import { humanizeApiError } from "../services/apiError";

const _MAX_ACTIVITY = 50;

// The WS close code the backend answers a refused board with (backend/api/routes/pipeline.py `pipeline_ws`).
// Every pipeline read is owner-or-ri, but the project LIST shows a Medior every project — so he can pin one he
// does not own and land here. A 4003 is an authorization VERDICT, not a network blip: retrying with the same
// token can only be refused again, forever, behind a board that looks calmly empty. It must stop the loop dead
// (every other close — idle timeout, redeploy, network drop — stays transient and keeps its backoff retry).
const _WS_CLOSE_FORBIDDEN = 4003;
// 4004 = this version does not exist. Also permanent, and it now ARRIVES: the accept() that had to move
// above the permission check so a 4003 could reach the browser put this close after a successful handshake
// too. The browser fired `open` (resetting the backoff to its first step and marking the socket as having
// connected), the server closed immediately, and `onclose` fell through to the reconnect path because only
// 4003 was latched — one full connect per second, forever, on the ordinary path of reopening Riadiace
// centrum with a pin to a version somebody has since deleted.
const _WS_CLOSE_VERSION_GONE = 4004;

/** The one plain-Slovak sentence for a permission refusal (mirrors the backend's 403 detail). Exported so
 *  every surface that must SAY it — Riadiace centrum, the sidebar entry — uses the same wording. */
export const PIPELINE_ACCESS_DENIED_MESSAGE = "Nemáš prístup k tomuto projektu.";
export const PIPELINE_VERSION_GONE_MESSAGE = "Táto verzia už neexistuje.";

/** True for the REST half of the same verdict (`GET /pipeline/{id}` and its `/messages` are owner-or-ri too). */
function isForbidden(err: unknown): boolean {
  return err instanceof ApiError && err.status === 403;
}

// CR-V2-018: an empty helper feed (count 0) ⇒ the Helpers panel hides.
const _EMPTY_HELPERS: HelpersFeed = { stage: "priprava", count: 0, line: "", helpers: [] };

// Live-activity survives a route change (2026-06-30 fix). The agent_activity stream is ephemeral — the WS
// never replays it on (re)connect — so when the Manažér leaves the build page (e.g. → Metriky) the page
// unmounts, the hook's `activity` state is destroyed, and on return it remounts empty → the feed flashed
// "Agent štartuje…" and the streamed lines were lost. This module-level, per-version cache lets a remount
// restore the buffer; it is kept in lock-step with `activity` and cleared (→ []) whenever a state change
// ends the run (so a settled run never shows stale activity).
const _activityCache = new Map<string, ActivityLine[]>();

export interface UsePipelineWs {
  board: PipelineBoard | null;
  connected: boolean;
  error: string | null;
  /** Live agent activity for the current run; reset on every state change. */
  activity: ActivityLine[];
  /** CR-V2-018: the AI Agent's ephemeral helper feed; ``count === 0`` ⇒ no helpers active (panel hidden).
   *  Reset on every state change (helpers belong to one run/turn). */
  helpers: HelpersFeed;
  /** CR-V2-015: the latest raw-PTY ``write_rejected`` reason (single-writer guard), or ``null``. Transient —
   *  the AI Agent tab shows a brief "engine práve pracuje" hint, then clears it. */
  writeRejected: string | null;
  /** Clear the transient ``writeRejected`` signal (after the hint has been shown). */
  clearWriteRejected: () => void;
  /** The socket dropped AFTER being established and is auto-reconnecting — drives a "stale" banner
   *  (false during the initial connect, so it never flashes on load). */
  reconnecting: boolean;
  /** This user may NOT read this version's pipeline (WS close 4003 / REST 403) — a PERMANENT refusal, not a
   *  transient drop: the socket stops reconnecting and `error` holds {@link PIPELINE_ACCESS_DENIED_MESSAGE}.
   *  Screens must SAY this (disabled with a reason / an explicit no-access panel) — an empty board reads as
   *  "Voľný", which is a lie about a permission denial. */
  accessDenied: boolean;
  /** This version no longer exists (WS close 4004) — permanent, like a refusal: retrying cannot help. */
  versionGone: boolean;
  /** Replace the board (e.g. with the fresh board returned by a POST action). */
  setBoard: (board: PipelineBoard) => void;
}

export function usePipelineWs(versionId: string | null): UsePipelineWs {
  const token = useAuthStore((s) => s.token);
  const isAway = usePresenceStore((s) => s.isAway);
  const [board, setBoard] = useState<PipelineBoard | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Restore the live-activity buffer on (re)mount so navigating away from Vývoj and back does not lose it
  // (2026-06-30 fix). The initializer reads the per-version cache; a new run / settle clears it (sync effect).
  const [activity, setActivity] = useState<ActivityLine[]>(() => (versionId ? (_activityCache.get(versionId) ?? []) : []));
  const [helpers, setHelpers] = useState<HelpersFeed>(_EMPTY_HELPERS);
  const [writeRejected, setWriteRejected] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const [versionGone, setVersionGone] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const everConnectedRef = useRef(false);
  // Mirrors `accessDenied` SYNCHRONOUSLY: the socket / REST handlers below run outside React's render cycle,
  // so a state read would still be `false` on the tick the refusal lands and one more retry would be armed.
  const deniedRef = useRef(false);

  // Latch a PERMANENT refusal (WS 4003 / REST 403). Kills the socket, stops the reconnect loop for good, and
  // OWNS `error` from here on — `scheduleReconnect` clears `error` on every drop, so without the latch the
  // refusal message would be wiped by the very loop it has to stop, leaving a calm empty board behind.
  const latchPermanent = useCallback((reason: "forbidden" | "gone") => {
    if (deniedRef.current) return;
    deniedRef.current = true;
    if (reason === "gone") setVersionGone(true);
    else setAccessDenied(true);
    setConnected(false);
    setReconnecting(false);
    setError(reason === "gone" ? PIPELINE_VERSION_GONE_MESSAGE : PIPELINE_ACCESS_DENIED_MESSAGE);
    const ws = wsRef.current;
    if (ws) {
      ws.onclose = null;
      ws.onerror = null; // detach BEFORE closing so this close cannot schedule one last retry
      try {
        ws.close();
      } catch {
        /* already closing */
      }
    }
  }, []);

  useEffect(() => {
    // A version / token change starts from a clean slate — a refusal latched for the PREVIOUS version must
    // never leak onto this one, and the reconnect loop below has to be allowed to run again.
    if (deniedRef.current) {
      deniedRef.current = false;
      setError(null);
    }
    setAccessDenied(false);
    setVersionGone(false);

    if (!versionId || !token) {
      setBoard(null);
      setConnected(false);
      setReconnecting(false);
      setActivity([]);
      setHelpers(_EMPTY_HELPERS);
      setWriteRejected(null);
      return;
    }

    let cancelled = false;
    let attempt = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    everConnectedRef.current = false;
    setReconnecting(false);

    // Fresh REST snapshot — on first mount AND on every WS reconnect, so a board that went stale
    // while the socket was down resyncs at once (incident 2026-06-12: a backend redeploy killed the
    // socket → with no reconnect the board froze → the Director's action buttons vanished until a
    // manual hard-refresh). WS also pushes one on connect, but REST fills the board before it opens.
    const fetchSnapshot = () => {
      getPipelineBoardApi(versionId)
        .then((b) => {
          if (!cancelled && !deniedRef.current) setBoard(b);
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          // A 403 is the SAME verdict the socket answers with 4003 (both gates are owner-or-ri) — permanent.
          // Latch it here too, so the refusal is named even if the close code never arrives (e.g. the socket
          // is still connecting, or a proxy swallowed the code).
          if (isForbidden(e)) {
            latchPermanent("forbidden");
            return;
          }
          if (deniedRef.current) return; // a latched refusal owns the messaging
          // Plain-Slovak framing — HonestStatusStrip renders this string raw, so it must never be a raw
          // English backend detail (audit Theme 2). We keep the hook's `error: string` shape and surface
          // only the manager-facing sentence.
          setError(humanizeApiError(e, "Načítanie prehľadu zlyhalo").message);
        });
    };

    const connect = () => {
      if (cancelled || deniedRef.current) return;
      fetchSnapshot();

      const ws = new WebSocket(buildPipelineWsUrl(versionId, token));
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled || deniedRef.current) return;
        // The backoff is NOT reset here. A handshake is not yet a working connection: the server can accept
        // and then close immediately (it must accept first for a 4003/4004 to be readable at all), and
        // resetting on `open` pinned the delay at its first step for every such close. It resets on the
        // first frame actually received — evidence the connection carries data.
        everConnectedRef.current = true;
        setConnected(true);
        setReconnecting(false);
        setError(null);
        // E6 (CR-NS-038): a fresh connection inherits the current away state. Read the LIVE value
        // (getState) — this effect is keyed on [versionId, token], not isAway, so the closure value
        // could be stale; the separate effect below pushes subsequent toggles.
        try {
          ws.send(JSON.stringify({ type: "presence", away: usePresenceStore.getState().isAway }));
        } catch {
          /* socket race — the toggle effect will resend on the next change */
        }
      };

      ws.onmessage = (ev) => {
        if (cancelled || deniedRef.current) return;
        // A frame arrived, so the connection is real — only now is the backoff earned back.
        attempt = 0;
        let frame: PipelineWsFrame;
        try {
          frame = JSON.parse(ev.data) as PipelineWsFrame;
        } catch {
          return; // malformed frame ignored
        }
        if (frame.type === "state_changed" && "board" in frame) {
          setBoard(frame.board);
          setActivity([]); // activity belongs to one run; a state change ends/starts it
          setHelpers(_EMPTY_HELPERS); // helpers belong to one turn; a settled state ends them
        } else if (frame.type === "state_changed" && "state" in frame) {
          setBoard((prev) =>
            prev ? { ...prev, state: frame.state } : { state: frame.state, recent_messages: [] },
          );
          setActivity([]);
          setHelpers(_EMPTY_HELPERS);
        } else if (frame.type === "message_added") {
          setBoard((prev) => {
            if (!prev) return { state: null, recent_messages: [frame.message] };
            if (prev.recent_messages.some((m) => m.id === frame.message.id)) return prev; // id-dedupe
            // Insert by authoritative seq (not arrival order) — robust even if frames race.
            const next = [...prev.recent_messages, frame.message].sort((a, b) => a.seq - b.seq);
            return { ...prev, recent_messages: next };
          });
        } else if (frame.type === "agent_activity") {
          const { stage, actor, kind, line } = frame;
          setActivity((prev) => [...prev, { stage, actor, kind, line }].slice(-_MAX_ACTIVITY));
        } else if (frame.type === "helpers") {
          // CR-V2-018: replace the live helper feed (count 0 ⇒ panel hides). The frame is authoritative —
          // it carries the FULL active set on every change, so we replace rather than accumulate.
          const { stage, count, line, helpers: descs } = frame;
          setHelpers({ stage, count, line, helpers: descs });
        } else if (frame.type === "write_rejected") {
          // CR-V2-015: the raw-PTY single-writer guard fired (break-glass keystroke during an engine turn).
          // Surface a transient hint; the AI Agent tab steers the Manažér to the relay instead.
          setWriteRejected(frame.reason || "engine práve pracuje");
        }
      };

      // Auto-reconnect with capped exponential backoff (CR 2026-06-12). Without this a dropped
      // socket (idle timeout, network blip, or a backend redeploy) froze the board permanently
      // until a manual refresh. onclose + onerror both route here; we detach the DEAD socket's
      // handlers (so it can't re-fire) and keep retryTimer non-null through connect() — together
      // those guarantee one drop schedules exactly one retry, with no double-socket.
      const scheduleReconnect = () => {
        if (cancelled || deniedRef.current) return; // a refused board never becomes reachable by retrying
        ws.onclose = null;
        ws.onerror = null; // this socket is dead — ignore any further events from it
        setConnected(false);
        setError(null); // the amber "reconnecting" banner now owns the connection messaging
        if (everConnectedRef.current) setReconnecting(true);
        if (retryTimer) return; // a retry is already pending
        const delay = Math.min(1000 * 2 ** attempt, 15000); // 1s,2s,4s,8s,…,15s cap
        attempt += 1;
        retryTimer = setTimeout(() => {
          connect(); // retryTimer stays non-null through connect() → blocks any re-entrant schedule
          retryTimer = null;
        }, delay);
      };

      // A close carrying the "forbidden" code is the ONE case that must not be retried — it is a verdict on
      // this user, not a broken pipe. Everything else (no code, any other code) stays transient.
      // `ev` is optional: `onerror` and non-DOM callers fire without a CloseEvent.
      ws.onclose = (ev?: CloseEvent) => {
        if (ev?.code === _WS_CLOSE_FORBIDDEN) {
          latchPermanent("forbidden");
          return;
        }
        if (ev?.code === _WS_CLOSE_VERSION_GONE) {
          // A version that does not exist will not start existing on retry.
          latchPermanent("gone");
          return;
        }
        scheduleReconnect();
      };
      ws.onerror = scheduleReconnect; // an error event carries no close code → treated as transient
    };

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      try {
        wsRef.current?.close();
      } catch {
        /* already closing */
      }
      wsRef.current = null;
    };
  }, [versionId, token, latchPermanent]);

  // Periodic reconcile (CR 2026-06-12): a SILENT safety net over the WS. Auto-reconnect heals a dropped
  // socket, but a board can still go stale on a LIVE socket if a single state_changed frame is missed
  // (or lost in a reconnect race) — leaving the Director with no action buttons. Re-fetching the
  // authoritative snapshot every 25s makes the board self-heal from ANY staleness within seconds, so a
  // manual hard-refresh is never needed. A failed tick is ignored (no error banner) — the next tick or
  // a live WS frame recovers. Independent of the socket lifecycle (keyed on [versionId, token]).
  useEffect(() => {
    if (!versionId || !token) return;
    // A tick can still be in flight when the pin moves to another version; its answer belongs to the version
    // it was asked about, so it must not land on (nor deny) the new one.
    let cancelled = false;
    const id = setInterval(() => {
      // A latched refusal stops this net too — otherwise every tick would 403 again, forever, on a board this
      // user is never allowed to read.
      if (deniedRef.current) return;
      getPipelineBoardApi(versionId)
        .then((snapshot) => {
          if (cancelled || deniedRef.current) return; // version changed / refused while this tick was in flight
          setBoard((prev) => {
            if (!prev) return snapshot;
            // Take the AUTHORITATIVE state + board-level fields from the snapshot (this is what unsticks
            // the action buttons), but UNION the messages so a WS frame that landed in the tiny window
            // around the reconcile's DB read isn't transiently clobbered (messages are append-only).
            const byId = new Map(snapshot.recent_messages.map((m) => [m.id, m] as const));
            for (const m of prev.recent_messages) if (!byId.has(m.id)) byId.set(m.id, m);
            return { ...snapshot, recent_messages: [...byId.values()].sort((a, b) => a.seq - b.seq) };
          });
        })
        .catch((e: unknown) => {
          // A 403 is the same permanent verdict as the socket's 4003 — latch it (the loop would otherwise
          // keep polling a door that never opens). Anything else is transient: the next tick (or the WS)
          // recovers, so it stays silent as before.
          if (!cancelled && isForbidden(e)) latchPermanent("forbidden");
        });
    }, 25_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [versionId, token, latchPermanent]);

  // E6 (CR-NS-038): push the away state live whenever it toggles, over the EXISTING socket — no
  // reconnect. On first mount / before open this no-ops (the onopen handler sends the initial state).
  useEffect(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "presence", away: isAway }));
      } catch {
        /* socket race — ignored */
      }
    }
  }, [isAway]);

  // Keep the per-version activity cache in lock-step with `activity` (2026-06-30 fix) — so a remount after a
  // route change restores the buffer (see _activityCache). A state change that resets `activity` to [] also
  // clears the cache here, so a settled run never restores stale activity.
  useEffect(() => {
    if (versionId) _activityCache.set(versionId, activity);
  }, [versionId, activity]);

  const replaceBoard = useCallback((b: PipelineBoard) => setBoard(b), []);
  const clearWriteRejected = useCallback(() => setWriteRejected(null), []);

  return {
    board,
    connected,
    error,
    activity,
    helpers,
    writeRejected,
    clearWriteRejected,
    reconnecting,
    accessDenied,
    versionGone,
    setBoard: replaceBoard,
  };
}
