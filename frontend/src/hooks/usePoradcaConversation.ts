// One conversation with Poradca: loading + live progress over WebSocket (ICCINT-167).
//
// The progress carries only STEPS (tool and target) and the end of the answer — never content. When it ends, the
// conversation is loaded again from the API, so the answer, cost and error come from stored state, not from an event.

import { useCallback, useEffect, useRef, useState } from "react";

import { useAuthStore } from "@/store/authStore";
import { buildPoradcaWsUrl, getPoradcaConversationApi } from "@/services/api/poradca";
import { ApiError } from "@/services/api";
import { humanizeApiError } from "@/services/apiError";
import type { PoradcaConversationDetail, PoradcaEvent } from "@/types/poradca";

const RECONNECT_MS = 2000;
const CLOSE_FORBIDDEN = 4003;
const CLOSE_GONE = 4004;

export interface UsePoradcaConversation {
  detail: PoradcaConversationDetail | null;
  error: string | null;
  reload: () => Promise<void>;
  setDetail: (d: PoradcaConversationDetail) => void;
}

export function usePoradcaConversation(conversationId: string | null): UsePoradcaConversation {
  const token = useAuthStore((s) => s.token);
  const [detail, setDetail] = useState<PoradcaConversationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const current = useRef<string | null>(conversationId);
  current.current = conversationId;

  const reload = useCallback(async () => {
    if (!conversationId) return;
    try {
      const d = await getPoradcaConversationApi(conversationId);
      if (current.current === conversationId) {
        setDetail(d);
        setError(null);
      }
    } catch (e: unknown) {
      if (current.current !== conversationId) return;
      // Deleted or foreign conversation: we do not leave the old text on screen.
      if (e instanceof ApiError && e.status === 404) setDetail(null);
      setError(humanizeApiError(e, "Rozhovor sa nepodarilo načítať").message);
    }
  }, [conversationId]);

  useEffect(() => {
    setDetail(null);
    setError(null);
    void reload();
  }, [reload]);

  useEffect(() => {
    if (!conversationId || !token) return;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let alive = true;

    const connect = () => {
      socket = new WebSocket(buildPoradcaWsUrl(conversationId, token));
      socket.onmessage = (msg) => {
        let evt: PoradcaEvent;
        try {
          evt = JSON.parse(String(msg.data)) as PoradcaEvent;
        } catch {
          return;
        }
        if (evt.type === "step") {
          setDetail((d) =>
            d
              ? {
                  ...d,
                  messages: (d.messages ?? []).map((m) =>
                    m.id === evt.message_id
                      ? { ...m, steps: [...(m.steps ?? []), { tool: evt.tool, target: evt.target }] }
                      : m,
                  ),
                }
              : d,
          );
        } else if (evt.type === "finished" || evt.type === "deleted") {
          // Deleted (e.g. in another tab): loading returns „Rozhovor sa nenašiel." instead of the old text.
          void reload();
        }
      };
      socket.onclose = (ev) => {
        if (!alive || ev.code === CLOSE_FORBIDDEN || ev.code === CLOSE_GONE) return;
        // Dropped connection: after reconnecting, load the state again — steps that arrived meanwhile are in the DB.
        timer = setTimeout(() => {
          void reload();
          connect();
        }, RECONNECT_MS);
      };
    };
    connect();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [conversationId, token, reload]);

  return { detail, error, reload, setDetail };
}
