"use client";

import { useRef, useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { ApiError, mutateJSON } from "@/lib/api";
import { useTypewriter } from "@/lib/useTypewriter";

const ACCEPT = ".pdf,.pptx,.docx,.md,.eml";

interface IngestPanelProps {
  /** Vault-relative target folder for uploads (e.g. `15-Courses/CS301`).
   * Omitted -> backend default (`00-Inbox/files`). */
  target?: string;
}

/**
 * INGEST panel (§11, §4 General) — real dropzone wired to `POST /api/ingest`
 * (multipart `file` + optional `target`), manual capture (`POST /api/capture`,
 * unchanged), and EMAIL.CAPTURE wired to `POST /api/ingest/email`
 * (flags.emailCapture: enabled) — extractions land in the Review queue, never
 * a direct write.
 */
export default function IngestPanel({ target }: IngestPanelProps) {
  const { show } = useToast();
  const [dragOver, setDragOver] = useState(false);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const { output, done: typingDone } = useTypewriter(status);

  async function upload(file: File) {
    setBusy(true);
    setStatus(`ingesting ${file.name} :: extract → chunk → embed (local)`);
    const body = new FormData();
    body.append("file", file);
    if (target) body.append("target", target);
    try {
      const response = await fetch("/api/ingest", { method: "POST", body });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string" ? payload.detail : `upload failed (${response.status})`,
        );
      }
      const { chunks, indexed } = payload as { chunks: number; indexed: boolean };
      setStatus(indexed ? `done :: ${file.name} indexed · ${chunks} chunks` : "saved — indexing unavailable");
    } catch (error) {
      setStatus("");
      show(`ingest :: failed — ${error instanceof Error ? error.message : "backend offline?"}`);
    } finally {
      setBusy(false);
    }
  }

  function pickFile(picked: File | null | undefined) {
    if (!picked || busy) return;
    upload(picked);
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

  // EMAIL.CAPTURE (§11) — real POST /api/ingest/email; results land in the
  // Review queue, never a direct write.
  const [email, setEmail] = useState("");
  const [emailBusy, setEmailBusy] = useState(false);

  async function extractEmail() {
    const text = email.trim();
    if (!text || emailBusy) return;
    setEmailBusy(true);
    try {
      const result = await mutateJSON<{ proposals: number; archived_path: string }>("/api/ingest/email", {
        text,
      });
      setEmail("");
      show(
        `email archived → ${result.archived_path} · ${result.proposals} proposal(s) in the Review queue`,
      );
    } catch (error) {
      show(`email :: extract failed — ${error instanceof ApiError ? error.message : "backend offline?"}`);
    } finally {
      setEmailBusy(false);
    }
  }

  return (
    <Panel label="INGEST">
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
      {status && (
        <p className="mt-2 font-mono text-[11px] text-ink-muted" aria-live="polite">
          {output}
          {busy && !typingDone && <span className="animate-blink text-[var(--ac)]">▊</span>}
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
        <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">email.capture</p>
        <textarea
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="paste an email…"
          rows={3}
          className="w-full resize-none border border-line bg-sunken px-3 py-2 text-[13px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
        />
        <button
          onClick={extractEmail}
          disabled={!email.trim() || emailBusy}
          className="mt-2 border border-line px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
        >
          {emailBusy ? "EXTRACTING…" : "EXTRACT →"}
        </button>
      </div>
    </Panel>
  );
}
