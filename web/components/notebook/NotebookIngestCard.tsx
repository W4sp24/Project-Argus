"use client";

import { useEffect, useRef, useState } from "react";
import IngestDialog from "@/components/sources/IngestDialog";
import IngestJobProgress from "@/components/sources/IngestJobProgress";
import NotebookPanel from "@/components/notebook/NotebookPanel";
import { useIngestJob, useStudyCourses } from "@/lib/api";

/**
 * "Add material" — the Notebook overview's way in for a lecture.
 *
 * Reuses the shared `IngestDialog`/`IngestJobProgress` rather than embedding
 * `components/dashboard/IngestPanel`: that panel is a whole `Panel` of its own
 * with email capture attached, shared with /dashboard, and nesting it here
 * would both double the surface and put a violet panel inside a slate one.
 * Everything real — destinations, note styles, dedupe, progress — still lives
 * in the dialog, exactly once.
 *
 * The dropzone is a `<label>` wrapping a real file input, not a div listening
 * for `drop`. A drop-only target cannot be reached from a keyboard, and it is
 * also what makes this testable: `setInputFiles` drives the identical handler.
 */
export default function NotebookIngestCard({ onIngested }: { onIngested?: () => void }) {
  const { data: courses } = useStudyCourses();
  const [course, setCourse] = useState("");
  const [files, setFiles] = useState<File[] | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const { data: job } = useIngestJob(jobId);

  /**
   * Tell the parent when the job *settles*, not when it is accepted.
   *
   * A course's materials count is derived from what is on disk, so revalidating
   * at queue time reads the state from before the file landed — the course row
   * stays at "0 materials" and its generators stay out of reach until something
   * unrelated refetches. Same rule `components/dashboard/IngestPanel.tsx`
   * follows, and for the same reason.
   *
   * Keyed on the job id rather than the status alone: `onIngested` is an inline
   * arrow at the call site, so it has a new identity on every render, and an
   * effect that depends on it would re-fire for as long as the job sits in a
   * terminal state — refetching, re-rendering, and refetching again.
   */
  const status = job?.status;
  const reported = useRef<string | null>(null);
  useEffect(() => {
    if (!jobId || reported.current === jobId) return;
    if (status === "ok" || status === "partial" || status === "failed") {
      reported.current = jobId;
      onIngested?.();
    }
  }, [jobId, status, onIngested]);

  const target = courses?.find((entry) => entry.code === course)?.materials_path;

  return (
    <>
      <NotebookPanel heading="Add material" scale="body" pad="md">
        <label
          className="block cursor-pointer rounded-tile border border-dashed border-nb-line p-6 text-center transition-colors hover:border-nb-lineHi"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            const dropped = Array.from(event.dataTransfer.files);
            if (dropped.length > 0) setFiles(dropped);
          }}
        >
          <input
            type="file"
            multiple
            className="sr-only"
            onChange={(event) => {
              const picked = Array.from(event.target.files ?? []);
              if (picked.length > 0) setFiles(picked);
              event.target.value = "";
            }}
          />
          <p className="text-ctl text-nb-ink">Drop a lecture here</p>
          <p className="mt-1 text-label text-nb-faint">PDF, PPTX, DOCX or Markdown</p>
        </label>

        {job && (
          <div className="mt-3">
            <IngestJobProgress job={job} onDismiss={() => setJobId(null)} />
          </div>
        )}

        <p className="mt-3 flex flex-wrap items-center gap-2 text-label text-nb-faint">
          <label htmlFor="notebook-ingest-course">Saving to</label>
          <select
            id="notebook-ingest-course"
            value={course}
            onChange={(event) => setCourse(event.target.value)}
            className="rounded-ctl border border-nb-line bg-nb-void px-2 py-1 text-label text-nb-body focus:border-nb-lineHi"
          >
            <option value="">00-Inbox/files</option>
            {(courses ?? []).map((entry) => (
              <option key={entry.code} value={entry.code}>
                {entry.code} materials
              </option>
            ))}
          </select>
        </p>
      </NotebookPanel>

      {files && (
        <IngestDialog
          initialFiles={files}
          lockedTarget={target}
          onClose={() => setFiles(null)}
          onStarted={(id) => {
            setJobId(id);
            setFiles(null);
          }}
        />
      )}
    </>
  );
}
