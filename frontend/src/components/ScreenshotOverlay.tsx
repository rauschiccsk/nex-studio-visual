/**
 * ScreenshotOverlay — captures the current page via html2canvas,
 * uploads the PNG to the backend (Ubuntu server), and shows a preview.
 *
 * Trigger: Ctrl+Shift+S keyboard shortcut (wired in App.tsx).
 * Dismiss: Escape key or clicking outside the modal.
 *
 * The file is saved server-side to /app/uploads/ (Ubuntu). There is no
 * client-side download — the browser download would land on the local
 * machine (e.g. Windows C:\), not on the Ubuntu server.
 */

import { useEffect, useRef, useState } from "react";
import html2canvas from "html2canvas";
import { Check, ClipboardCopy, Loader2, X } from "lucide-react";

import api from "@/services/api";

interface Props {
  onClose: () => void;
}

interface UploadResult {
  filename: string;
  size: number;
}

type Phase = "capturing" | "uploading" | "done" | "error";

export function ScreenshotOverlay({ onClose }: Props) {
  const [phase, setPhase] = useState<Phase>("capturing");
  const [dataUrl, setDataUrl] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copyDone, setCopyDone] = useState(false);
  const overlayRef = useRef<HTMLDivElement>(null);

  const handleCopy = (text: string) => {
    const flash = () => {
      setCopyDone(true);
      setTimeout(() => setCopyDone(false), 1500);
    };
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(flash).catch(() => {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        flash();
      });
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      flash();
    }
  };

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

        // Convert to Blob and upload to backend (saved on Ubuntu server)
        const blob = await (await fetch(url)).blob();
        const form = new FormData();
        form.append("file", blob, "screenshot.png");

        const result = await api.post<UploadResult>("/uploads/screenshot", form);

        if (!cancelled) {
          setUploadResult(result);
          setPhase("done");
        }
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
            <><Check className="h-4 w-4 text-green-400" /><span className="text-green-400">Uložené na serveri</span></>
          )}
          {phase === "error" && (
            <span className="text-red-400">{error}</span>
          )}
        </div>

        {/* Server path */}
        {phase === "done" && uploadResult && (
          <div className="mb-4 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2">
            <p className="mb-0.5 text-[10px] uppercase tracking-wider text-gray-500">Cesta na serveri</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs text-green-300">uploads/{uploadResult.filename}</code>
              <button
                onClick={() => handleCopy(`uploads/${uploadResult.filename}`)}
                title="Copy path"
                className={`flex items-center gap-1 rounded px-2 py-1 text-xs transition-colors ${copyDone ? "text-green-400" : "text-gray-400 hover:bg-gray-800 hover:text-gray-200"}`}
              >
                {copyDone ? (
                  <><Check className="h-3.5 w-3.5" />Copied!</>
                ) : (
                  <><ClipboardCopy className="h-3.5 w-3.5" />Copy</>
                )}
              </button>
            </div>
            <p className="mt-1 text-[10px] text-gray-500">
              {(uploadResult.size / 1024).toFixed(1)} KB
            </p>
          </div>
        )}

        {/* Preview */}
        {dataUrl && (
          <img
            src={dataUrl}
            alt="Screenshot preview"
            className="mb-4 w-full rounded-lg border border-gray-700 object-contain"
            style={{ maxHeight: "280px" }}
          />
        )}

        {/* Actions */}
        <div className="flex justify-end">
          <button
            onClick={onClose}
            className="rounded-lg bg-gray-700 px-4 py-1.5 text-xs font-medium text-gray-200 hover:bg-gray-600"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
