/**
 * ScreenshotOverlay — captures the current page via html2canvas,
 * uploads the PNG to the backend, and shows a preview with a copy-link action.
 *
 * Trigger: Ctrl+Shift+S keyboard shortcut (wired in App.tsx).
 * Dismiss: Escape key or clicking outside the modal.
 */

import { useEffect, useRef, useState } from "react";
import html2canvas from "html2canvas";
import { Check, Download, Loader2, X } from "lucide-react";

import api from "@/services/api";

interface Props {
  onClose: () => void;
}

type Phase = "capturing" | "uploading" | "done" | "error";

export function ScreenshotOverlay({ onClose }: Props) {
  const [phase, setPhase] = useState<Phase>("capturing");
  const [dataUrl, setDataUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  // Close on Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;

    async function capture() {
      try {
        // Give the overlay a frame to render before capturing (avoid self-capture)
        await new Promise((r) => requestAnimationFrame(r));

        const canvas = await html2canvas(document.body, {
          useCORS: true,
          ignoreElements: (el) => el === overlayRef.current,
        });

        if (cancelled) return;

        const url = canvas.toDataURL("image/png");
        setDataUrl(url);
        setPhase("uploading");

        // Convert to Blob and upload
        const blob = await (await fetch(url)).blob();
        const form = new FormData();
        form.append("file", blob, "screenshot.png");

        await api.post("/uploads/screenshot", form);

        if (!cancelled) setPhase("done");
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Screenshot failed");
          setPhase("error");
        }
      }
    }

    capture();
    return () => { cancelled = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDownload = () => {
    if (!dataUrl) return;
    const a = document.createElement("a");
    a.href = dataUrl;
    a.download = `nex-studio-${Date.now()}.png`;
    a.click();
  };

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="relative w-[480px] max-w-[90vw] rounded-xl border border-gray-700 bg-gray-800 p-5 shadow-2xl">
        {/* Close */}
        <button
          onClick={onClose}
          className="absolute right-3 top-3 rounded p-1 text-gray-400 hover:bg-gray-700 hover:text-gray-200"
        >
          <X className="h-4 w-4" />
        </button>

        <h2 className="mb-4 text-sm font-semibold text-gray-100">Screenshot</h2>

        {/* Status */}
        <div className="mb-4 flex items-center gap-2 text-xs text-gray-400">
          {phase === "capturing" && (
            <><Loader2 className="h-4 w-4 animate-spin text-indigo-400" />Capturing page…</>
          )}
          {phase === "uploading" && (
            <><Loader2 className="h-4 w-4 animate-spin text-indigo-400" />Uploading…</>
          )}
          {phase === "done" && (
            <><Check className="h-4 w-4 text-green-400" /><span className="text-green-400">Screenshot saved</span></>
          )}
          {phase === "error" && (
            <span className="text-red-400">{error}</span>
          )}
        </div>

        {/* Preview */}
        {dataUrl && (
          <img
            src={dataUrl}
            alt="Screenshot preview"
            className="mb-4 w-full rounded-lg border border-gray-700 object-contain"
            style={{ maxHeight: "300px" }}
          />
        )}

        {/* Actions */}
        {dataUrl && (
          <div className="flex gap-2">
            <button
              onClick={handleDownload}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500"
            >
              <Download className="h-3.5 w-3.5" />
              Download PNG
            </button>
            <button
              onClick={onClose}
              className="rounded-lg bg-gray-700 px-3 py-1.5 text-xs font-medium text-gray-200 hover:bg-gray-600"
            >
              Close
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
