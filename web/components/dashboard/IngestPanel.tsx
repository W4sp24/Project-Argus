"use client";

import { useRef, useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import { ApiError, apiFetch, mutateJSON } from "@/lib/api";
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
      const response = await apiFetch("/api/ingest", { method: "POST", body });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string" ? payload.detail : `upload failed (${response.status})`,
        );
      }
      const { chunks, indexed, index_error } = payload as {
        chunks: number;
        indexed: boolean;
        index_error: string | null;
      };
      if (indexed) {
        setStatus(`done :: ${file.name} indexed · ${chunks} chunks`);
      } else if (index_error) {
        // A real failure (broken index), not just "no [rag] extras installed".
        setStatus(`saved — indexing failed: ${index_error}`);
      } else {
        setStatus("saved — indexing unavailable");
      }
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
    const response = await apiFetch("/api/capture", {
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
      {/* A <button>, not a clickable <div>: the dropzone was mouse-only, with
          no role, no tabindex and no key handler. */}
      <button
        type="button"
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
        className={`w-full cursor-pointer border border-dashed px-4 py-6 text-center transition-[border-color,background-color] ${
          dragOver ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
        }`}
      >
        <span className="block font-mono text-label text-ink-muted">
          drop a file, or click to choose ({ACCEPT.replaceAll(",", " ")})
        </span>
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        aria-hidden
        tabIndex={-1}
        className="hidden"
        onChange={(event) => pickFile(event.target.files?.[0])}
      />
      {status && (
        <p className="mt-2 font-mono text-label text-ink-muted" aria-live="polite">
          {output}
          {busy && !typingDone && <span className="animate-blink text-[var(--ac)]">▊</span>}
        </p>
      )}
      <p className="mt-2 font-mono text-meta text-ink-faint">
        files are indexed locally — nothing leaves your machine
      </p>

      <form onSubmit={submitCapture} className="mt-4 flex gap-2 border-t border-line pt-4">
        <input
          value={capture}
          onChange={(event) => setCapture(event.target.value)}
          placeholder="e.g. email prof about thesis"
          aria-label="Capture a task or note"
          className="min-w-0 flex-1 border border-line bg-sunken px-3 py-2 text-body placeholder:text-ink-faint focus:border-lineHi"
        />
        <Button type="submit" size="md" disabled={!capture.trim()} className="shrink-0">
          Save
        </Button>
      </form>
      {captureStatus && <p className="mt-2 font-mono text-label text-[var(--ac)]">{captureStatus}</p>}

      <div className="mt-4 border-t border-line pt-4">
        {/* A real <label>, not a paragraph that happens to sit above the box. */}
        <label
          htmlFor="ingest-email"
          className="mb-2 block font-mono text-meta uppercase tracking-[0.16em] text-ink-faint"
        >
          email.capture
        </label>
        <textarea
          id="ingest-email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="paste an email…"
          rows={3}
          className="w-full resize-none border border-line bg-sunken px-3 py-2 text-body placeholder:text-ink-faint focus:border-lineHi"
        />
        <Button
          size="md"
          onClick={extractEmail}
          disabled={!email.trim() || emailBusy}
          className="mt-2"
        >
          {emailBusy ? "EXTRACTING…" : "EXTRACT →"}
        </Button>
      </div>
    </Panel>
  );
}
