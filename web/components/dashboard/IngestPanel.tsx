"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import IngestDialog from "@/components/sources/IngestDialog";
import IngestJobProgress from "@/components/sources/IngestJobProgress";
import Button from "@/components/ui/Button";
import { ApiError, apiFetch, mutateJSON, useIngestJob } from "@/lib/api";

interface IngestPanelProps {
  /** Vault-relative target folder for uploads (e.g. a course's materials
   * folder — see `CourseInfo.materials_path`, never a hand-built path).
   * Omitted -> backend default (`00-Inbox/files`). */
  target?: string;
  /**
   * Called after a successful upload (saved, whether or not indexing
   * finished yet). Lets a parent whose own counts are derived from this
   * `target` — Study's per-course materials count, in particular —
   * revalidate instead of staying stale until some unrelated refetch. Was
   * missing entirely, which was half of the reported "ingesting files seems
   * to not work" bug: the file really did save, but nothing ever told the
   * Study page's course list to look again.
   */
  onUploaded?: () => void;
}

/**
 * INGEST panel (§11, §4 General) — the dropzone opens the shared
 * `IngestDialog`, manual capture posts to `POST /api/capture` (unchanged), and
 * EMAIL.CAPTURE to `POST /api/ingest/email` (flags.emailCapture: enabled),
 * whose extractions land in the Review queue rather than as a direct write.
 *
 * The dropzone used to post to the single-file synchronous `POST /api/ingest`
 * with a `target` the dashboard never supplied, so every hand-dropped file
 * landed in the inbox with no destination choice, no note style and no
 * progress. Two ingest paths sat inches apart with very different
 * capabilities and the worse one was the discoverable one. There is one now;
 * the legacy endpoint stays for API callers, but no UI reaches it.
 */
export default function IngestPanel({ target, onUploaded }: IngestPanelProps) {
  const { show } = useToast();
  const [dragOver, setDragOver] = useState(false);

  const [capture, setCapture] = useState("");
  const [captureStatus, setCaptureStatus] = useState<string | null>(null);
  const [dialogFiles, setDialogFiles] = useState<File[] | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: job } = useIngestJob(jobId);

  // Tell the parent once the job settles, not when the upload is accepted:
  // the files a caller's counts are derived from do not exist until then.
  const status = job?.status;
  useEffect(() => {
    if (status === "ok" || status === "partial" || status === "failed") onUploaded?.();
  }, [status, onUploaded]);

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
    <Panel
      label="INGEST"
      headerRight={
        <Link
          href="/sources"
          // Was the only link to /sources in the app, in the lowest-contrast
          // colour in the palette. It is not decoration.
          className="font-mono text-meta uppercase tracking-wide text-ink-muted transition-colors hover:text-[var(--ac)]"
        >
          sources →
        </Link>
      }
    >
      {/* A <button>, not a clickable <div>: the dropzone was mouse-only, with
          no role, no tabindex and no key handler. It opens the shared dialog
          now, so a drop here gets the same destination choice, note style,
          collision precheck and per-file progress as /sources -- and a
          dropped file carries straight through rather than being re-asked
          for. */}
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
          setDialogFiles(Array.from(event.dataTransfer.files ?? []));
        }}
        onClick={() => setDialogFiles([])}
        className={`w-full cursor-pointer border border-dashed px-4 py-6 text-center transition-[border-color,background-color] ${
          dragOver ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
        }`}
      >
        <span className="block font-mono text-label text-ink-muted">
          drop a file, or click to choose
        </span>
      </button>
      {job && (
        <div className="mt-3 border border-line px-3 py-2">
          <IngestJobProgress job={job} />
        </div>
      )}

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
      {dialogFiles !== null && (
        <IngestDialog
          onClose={() => setDialogFiles(null)}
          onStarted={setJobId}
          lockedTarget={target}
          initialFiles={dialogFiles}
        />
      )}
    </Panel>
  );
}
