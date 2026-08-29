/**
 * useDraft — a half-written message survives leaving the screen (ICCINT-30).
 *
 * The Director typed a message to the agent, clicked over to Dokumenty to check what he was about to say,
 * came back, and the box was empty. Nothing warned him. Every writing surface behaved the same way, because
 * each held its text in `useState` and a route change unmounts the component.
 *
 * These pin the behaviour that matters, not the storage mechanism: the text comes back, it is cleared only
 * when it actually went somewhere, two builds never share a draft, and a broken localStorage cannot take the
 * screen down with it.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

import { useDraft, draftKey } from "@/hooks/useDraft";

beforeEach(() => {
  window.localStorage.clear();
});

describe("useDraft", () => {
  it("brings the text back after the screen was left and re-entered", () => {
    const key = draftKey("rozhovor", "v1");
    const first = renderHook(() => useDraft(key));
    act(() => first.result.current.setText("Rozpísaná správa agentovi"));
    first.unmount(); // ← leaving for Dokumenty

    const second = renderHook(() => useDraft(key));
    expect(second.result.current.text).toBe("Rozpísaná správa agentovi");
  });

  it("marks a restored draft as restored, and stops once he types", () => {
    const key = draftKey("rozhovor", "v1");
    const first = renderHook(() => useDraft(key));
    act(() => first.result.current.setText("koncept"));
    first.unmount();

    const second = renderHook(() => useDraft(key));
    // Text that appears by itself has to be recognisable as HIS earlier draft.
    expect(second.result.current.restored).toBe(true);
    act(() => second.result.current.setText("koncept a niečo ďalšie"));
    expect(second.result.current.restored).toBe(false);
  });

  it("clears ONLY on a real send, never on leaving", () => {
    const key = draftKey("rozhovor", "v1");
    const hook = renderHook(() => useDraft(key));
    act(() => hook.result.current.setText("text"));
    act(() => hook.result.current.clear()); // what a successful send calls
    hook.unmount();

    expect(renderHook(() => useDraft(key)).result.current.text).toBe("");
  });

  it("keeps two builds apart — a draft must never surface under the wrong project", () => {
    const a = renderHook(() => useDraft(draftKey("rozhovor", "v1")));
    act(() => a.result.current.setText("pre prvý projekt"));
    const b = renderHook(() => useDraft(draftKey("rozhovor", "v2")));
    expect(b.result.current.text).toBe("");
  });

  it("keeps two surfaces of one build apart", () => {
    const chat = renderHook(() => useDraft(draftKey("rozhovor", "v1")));
    act(() => chat.result.current.setText("do rozhovoru"));
    const answer = renderHook(() => useDraft(draftKey("odpoved", "v1")));
    expect(answer.result.current.text).toBe("");
  });

  it("without a build there is nothing to key a draft to, and that is not an error", () => {
    const hook = renderHook(() => useDraft(draftKey("rozhovor", null)));
    act(() => hook.result.current.setText("text"));
    expect(hook.result.current.text).toBe("text"); // typing still works…
    expect(window.localStorage.length).toBe(0); // …it is simply not persisted
  });

  it("a refusing localStorage must not take the screen down", () => {
    // Private mode, quota exceeded, storage disabled — a draft is a convenience. Losing it is acceptable;
    // throwing out of a keystroke handler is not.
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    const hook = renderHook(() => useDraft(draftKey("rozhovor", "v1")));
    expect(() => act(() => hook.result.current.setText("text"))).not.toThrow();
    expect(hook.result.current.text).toBe("text");
    setItem.mockRestore();
  });
});
