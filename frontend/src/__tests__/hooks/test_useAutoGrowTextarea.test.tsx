/**
 * DEV-390 — a box that appears later with the same text is sized too.
 *
 * 15.11.2026 the answer box of Riadiace centrum came back from Poradca on two rows: its text is restored at once, the
 * box appears only once the build's state has loaded, and a height driven by the text alone was never measured. jsdom
 * lays nothing out, so the box's natural height is given here (scrollHeight); the real browser is e2e
 * control-center-box.spec.ts.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { useState } from "react";

import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";

const TEXT = "1. bod\n2. bod\n3. bod\n4. bod";
const NATURAL = 96;

function Box({ initiallyShown, value = TEXT }: { initiallyShown: boolean; value?: string }) {
  const [shown, setShown] = useState(initiallyShown);
  const ref = useAutoGrowTextarea(value);
  return (
    <>
      <button type="button" onClick={() => setShown(true)}>
        ukáž
      </button>
      {shown && <textarea aria-label="pole" ref={ref} value={value} readOnly />}
    </>
  );
}

describe("useAutoGrowTextarea (DEV-390)", () => {
  let descriptor: PropertyDescriptor | undefined;
  beforeEach(() => {
    descriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight");
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get: () => NATURAL });
  });
  afterEach(() => {
    if (descriptor) Object.defineProperty(HTMLElement.prototype, "scrollHeight", descriptor);
    else delete (HTMLElement.prototype as { scrollHeight?: number }).scrollHeight;
  });

  it("sizes a box that is there from the start", () => {
    render(<Box initiallyShown />);
    expect(screen.getByLabelText("pole").style.height).toBe(`${NATURAL}px`);
  });

  it("⚠️ sizes a box that appears later with the same text", () => {
    render(<Box initiallyShown={false} />);

    act(() => screen.getByRole("button", { name: "ukáž" }).click());

    expect(screen.getByLabelText("pole").style.height).toBe(`${NATURAL}px`);
  });

  it("sizes the box again when its width changes, and only then", () => {
    let width = 300;
    const widthDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => width });
    try {
      render(<Box initiallyShown />);
      const box = screen.getByLabelText("pole");
      Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get: () => 150 });

      act(() => void window.dispatchEvent(new Event("resize")));
      expect(box.style.height).toBe(`${NATURAL}px`); // the same width wraps the same — nothing to do

      width = 200; // a narrower panel wraps the text onto more rows
      act(() => void window.dispatchEvent(new Event("resize")));
      expect(box.style.height).toBe("150px");
    } finally {
      if (widthDescriptor) Object.defineProperty(HTMLElement.prototype, "clientWidth", widthDescriptor);
    }
  });

  it("caps the height at twenty rows and lets the rest scroll", () => {
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get: () => 5000 });
    render(<Box initiallyShown />);

    const box = screen.getByLabelText("pole");
    expect([box.style.height, box.style.overflowY]).toEqual(["400px", "auto"]);
  });
});
