"use client";

import { useEffect, useRef, useState } from "react";
import Panel from "@/components/Panel";
import { useTypewriter } from "@/lib/useTypewriter";

const ACCEPT = ".pdf,.pptx,.docx,.md,.eml";

function PreviewTag() {
  return (
    <span className="border border-[#3d2f66] px-1 py-px font-mono text-[8px] uppercase tracking-[0.16em] text-[#8b7bc0]">
      PREVIEW
    </span>
  );
}

/**
 * INGEST panel (§11, §4 General) — real manual text-capture (POST
 * /api/capture) plus a [PREVIEW] file dropzone and EMAIL.CAPTURE: the
 * `/api/ingest` + `/api/ingest/email` endpoints aren't in this branch's
 * ancestry (§ decoupling rule), so drops/extracts play the typed status
 * line as mock feedback only — no upload, no write.
 */
export default function IngestPanel() {
  const [dragOver, setDragOver] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<"idle" | "ingesting" | "done">("idle");
  const inputRef = useRef<HTMLInputElement>(null);

  const chunks = file ? 3 + (file.name.length % 9) : 0;
  const statusText =
    phase === "ingesting"
      ? `ingesting ${file?.name} :: extract → chunk (${chunks}) → embed (local)`
      : phase === "done"
        ? `done :: ${file?.name} indexed · ${chunks} chunks`
        : "";
  const { output, done: typingDone } = useTypewriter(statusText);

  useEffect(() => {
    if (phase !== "ingesting" || !typingDone) return;
    const timer = window.setTimeout(() => setPhase("done"), 350);
    return () => window.clearTimeout(timer);
  }, [phase, typingDone]);

  function pickFile(picked: File | null | undefined) {
    if (!picked) return;
    setFile(picked);
    setPhase("ingesting");
  }

  // Manual capture — real writer path (POST /api/capture), unchanged from CaptureCard.
  const [capture, setCapture] = useState("");
  const [captureStatus, setCaptureStatus] = useState<string | null>(null);

  async function submitCapture(event: React.FormEvent) {
    event.preventDefault();
    const text = capture.trim();
    if (!text) return;
    setCapture("");
    const response = await fetch("/api/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const payload = await response.json();
    setCaptureStatus(response.ok ? `Captured → ${payload.path}` : `Capture failed: ${payload.detail}`);
    setTimeout(() => setCaptureStatus(null), 5000);
  }

  // EMAIL.CAPTURE — [PREVIEW] mock extraction only, never a real write.
  const [email, setEmail] = useState("");
  const [extracted, setExtracted] = useState<string | null>(null);

  function extractEmail() {
    if (!email.trim()) return;
    setExtracted("preview :: would extract tasks/dates/contacts → proposals land in the review queue");
  }

  return (
    <Panel label="INGEST">
      <div className="mb-2 flex items-center gap-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">files</p>
        <PreviewTag />
      </div>
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragOver(false);
          pickFile(event.dataTransfer.files?.[0]);
        }}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer border border-dashed px-4 py-6 text-center transition-[border-color,background-color] ${
          dragOver ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
        }`}
      >
        <p className="font-mono text-[11px] text-ink-muted">
          drop a file, or click to choose ({ACCEPT.replaceAll(",", " ")})
        </p>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          onChange={(event) => pickFile(event.target.files?.[0])}
        />
      </div>
      {phase !== "idle" && (
        <p className="mt-2 font-mono text-[11px] text-ink-muted" aria-live="polite">
          {output}
          {phase === "ingesting" && <span className="animate-blink text-[var(--ac)]">▊</span>}
        </p>
      )}
      <p className="mt-2 font-mono text-[10px] text-ink-faint">
        files are indexed locally — nothing leaves your machine
      </p>

      <form onSubmit={submitCapture} className="mt-4 flex gap-2 border-t border-line pt-4">
        <input
          value={capture}
          onChange={(event) => setCapture(event.target.value)}
          placeholder="e.g. email prof about thesis"
          className="min-w-0 flex-1 border border-line bg-sunken px-3 py-2 text-[13px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
        />
        <button
          type="submit"
          disabled={!capture.trim()}
          className="shrink-0 border border-line px-3 py-2 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
        >
          Save
        </button>
      </form>
      {captureStatus && <p className="mt-2 font-mono text-[11px] text-[var(--ac)]">{captureStatus}</p>}

      <div className="mt-4 border-t border-line pt-4">
        <div className="mb-2 flex items-center gap-2">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">email.capture</p>
          <PreviewTag />
        </div>
        <textarea
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="paste an email…"
          rows={3}
          className="w-full resize-none border border-line bg-sunken px-3 py-2 text-[13px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
        />
        <button
          onClick={extractEmail}
          disabled={!email.trim()}
          className="mt-2 border border-line px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
        >
          EXTRACT →
        </button>
        {extracted && <p className="mt-2 font-mono text-[11px] text-ink-muted">{extracted}</p>}
      </div>
    </Panel>
  );
}
