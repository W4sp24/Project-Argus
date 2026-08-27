"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import { useConfirm } from "@/components/ui/useConfirm";
import {
  ApiError,
  hashFile,
  precheckIngest,
  startIngestJob,
  useIngestDestinations,
  useIngestNoteStyles,
  useModels,
} from "@/lib/api";
import { useSelectedModel } from "@/lib/models";

const ACCEPT = ".pdf,.pptx,.docx,.md,.eml";
const MAX_FILES = 50;
/** The "don't write anything" option. Not a style key the backend knows. */
const NO_NOTE = "";

/** What the precheck found about one picked file, once it has answered. */
interface Collision {
  /** The picked file's name already exists at the destination. */
  exists: boolean;
  /** …and holds exactly these bytes, so re-adding it changes nothing. */
  identical: boolean;
}

interface Picked {
  file: File;
  collision?: Collision;
}

function collisionNote(picked: Picked, replace: boolean): string | null {
  if (!picked.collision?.exists) return null;
  if (picked.collision.identical) {
    return replace ? "already ingested — will be rewritten" : "already ingested — will be copied";
  }
  return replace ? "replaces the copy in your vault" : "saved alongside as a second copy";
}

export default function IngestDialog({
  onClose,
  onStarted,
  initialTarget,
  lockedTarget,
  defaultNoteStyle,
}: {
  onClose: () => void;
  /** Handed the new job id so the page can start polling it. */
  onStarted: (jobId: string) => void;
  initialTarget?: string;
  /**
   * Pin the destination and hide the picker. The Course Hub opens this
   * already knowing where the files go — its course's `materials_path`, taken
   * from the API rather than built here, because a literal
   * `15-Courses/<CODE>/materials` in the frontend is the exact bug the
   * configurable-taxonomy refactor fixed.
   */
  lockedTarget?: string;
  /** Which note style is preselected. The Course Hub opens on "Summary",
   * because ingesting a lecture in order to get notes from it is the reason
   * that entry point exists at all. */
  defaultNoteStyle?: string;
}) {
  const { data: destinations } = useIngestDestinations();
  const { data: noteStyles } = useIngestNoteStyles();
  const { data: models } = useModels();
  const modelName = useSelectedModel();
  const { confirm, confirmDialog } = useConfirm();

  const [picked, setPicked] = useState<Picked[]>([]);
  const [target, setTarget] = useState(lockedTarget ?? initialTarget ?? "");
  const [style, setStyle] = useState(defaultNoteStyle ?? NO_NOTE);
  const [prompt, setPrompt] = useState("");
  const [replace, setReplace] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLButtonElement>(null);

  // Memoised because it is an effect dependency: a fresh [] every render
  // would re-run the default-target effect on every keystroke.
  const options = useMemo(() => destinations?.destinations ?? [], [destinations]);
  useEffect(() => {
    if (lockedTarget || target || !options.length) return;
    setTarget(options[0]);
  }, [lockedTarget, options, target]);

  // The badge is the honest half of the trust line: a hosted model means the
  // file's text leaves this machine, and the panel has promised the opposite
  // since it shipped. `local` comes from the registry, not from `builtin` — a
  // self-hosted open-weight endpoint is still a network hop.
  const isLocal = models?.find((model) => model.name === modelName)?.local ?? false;
  // Either half asks for a note. Keying this off the textarea alone — as it
  // used to — meant picking "Study guide" and typing nothing sent every file
  // to a hosted provider with no warning at all.
  const wantsNote = style !== NO_NOTE || prompt.trim().length > 0;

  async function add(files: FileList | File[] | null) {
    if (!files) return;
    const incoming = Array.from(files).slice(0, MAX_FILES - picked.length);
    if (!incoming.length) return;
    setError(null);
    setPicked((current) => [...current, ...incoming.map((file) => ({ file }))]);

    // Ask what is already there, so the collision is shown before the upload
    // rather than discovered as a mystery `-2` afterwards.
    const destination = target || options[0];
    if (!destination) return;
    await Promise.all(
      incoming.map(async (file) => {
        try {
          const [found, digest] = await Promise.all([
            precheckIngest(file.name, destination),
            hashFile(file),
          ]);
          setPicked((current) =>
            current.map((entry) =>
              entry.file === file
                ? {
                    ...entry,
                    collision: {
                      exists: found.exists,
                      identical: Boolean(found.sha256 && digest && found.sha256 === digest),
                    },
                  }
                : entry,
            ),
          );
        } catch {
          // A precheck that cannot answer must not block the ingest; the user
          // just loses the advance warning about the collision.
        }
      }),
    );
  }

  async function run() {
    if (!picked.length || busy) return;
    if (wantsNote && !isLocal) {
      const answer = await confirm({
        label: "Send these files to a provider",
        message: `Writing notes sends the text of ${picked.length} file${
          picked.length === 1 ? "" : "s"
        } to ${modelName}, which runs off this machine.`,
        detail:
          "The files are still indexed locally either way. Notes tagged #no-ai are never sent, " +
          'and are reported as skipped. Choose “Don’t write a note” to ingest without one.',
        confirmLabel: "Send and ingest",
        tone: "primary",
      });
      if (answer === null) return;
    }

    setBusy(true);
    setError(null);
    try {
      const jobId = await startIngestJob(
        picked.map((entry) => entry.file),
        { target, noteStyle: style, summaryPrompt: prompt.trim(), replace },
      );
      onStarted(jobId);
      onClose();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Could not reach Argus. Is the backend running?",
      );
      setBusy(false);
    }
  }

  const collides = picked.some((entry) => entry.collision?.exists);
  const styles = noteStyles?.styles ?? [];
  // The chosen style's own description, so the hint says what this note will
  // actually contain instead of restating that a note gets written.
  const styleHint =
    styles.find((option) => option.key === style)?.description ??
    "Saved as markdown beside the file — or in the course's notes/ folder — and indexed too.";

  return (
    <>
      <Dialog
        label="Ingest files"
        onClose={onClose}
        align="center"
        className="w-full max-w-xl p-5"
        initialFocusRef={dropRef}
      >
        <p className="eyebrow mb-3">▍ingest</p>

        <button
          ref={dropRef}
          type="button"
          onDragOver={(event) => {
            event.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragOver(false);
            void add(event.dataTransfer.files);
          }}
          onClick={() => inputRef.current?.click()}
          className={`w-full border border-dashed px-4 py-6 text-center transition-[border-color,background-color] ${
            dragOver ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
          }`}
        >
          <span className="block font-mono text-label text-ink-muted">
            drop files, or click to choose
          </span>
          <span className="mt-1 block font-mono text-meta text-ink-faint">
            {ACCEPT.replaceAll(",", " ")} · up to {MAX_FILES} at once
          </span>
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          aria-hidden
          tabIndex={-1}
          className="hidden"
          onChange={(event) => void add(event.target.files)}
        />

        {picked.length > 0 && (
          <ul className="mt-3 flex max-h-40 flex-col gap-1 overflow-y-auto">
            {picked.map((entry, index) => {
              const note = collisionNote(entry, replace);
              return (
                <li
                  key={`${entry.file.name}-${index}`}
                  className="flex items-baseline gap-2 border border-line px-2 py-1"
                >
                  <span className="min-w-0 flex-1 truncate text-label text-ink">
                    {entry.file.name}
                  </span>
                  {note && <span className="shrink-0 font-mono text-meta text-warn">{note}</span>}
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove ${entry.file.name}`}
                    onClick={() => setPicked((current) => current.filter((_, i) => i !== index))}
                  >
                    ×
                  </Button>
                </li>
              );
            })}
          </ul>
        )}

        <div className="mt-4 flex flex-col gap-4">
          {lockedTarget ? (
            <p className="font-mono text-meta text-ink-faint">
              Saving to <span className="text-ink-muted">{lockedTarget}</span>
            </p>
          ) : (
            <Field label="Save to" hint="Where the files land in your vault.">
              {(props) => (
                <select
                  {...props}
                  value={target}
                  onChange={(event) => setTarget(event.target.value)}
                  className={FIELD_CONTROL}
                >
                  {options.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          )}

          {collides && (
            <label className="flex items-start gap-2">
              <input
                type="checkbox"
                checked={replace}
                onChange={(event) => setReplace(event.target.checked)}
                className="mt-0.5 accent-[var(--ac)]"
              />
              <span className="text-label text-ink">
                Replace the copies already in my vault
                <span className="mt-0.5 block font-mono text-meta text-ink-faint">
                  Without this, both copies stay indexed and an answer can cite either. Your vault
                  is snapshotted first, so a replace is one `git revert` away.
                </span>
              </span>
            </label>
          )}

          <Field label="Write a note from each file" hint={styleHint}>
            {(props) => (
              <select
                {...props}
                value={style}
                onChange={(event) => setStyle(event.target.value)}
                className={FIELD_CONTROL}
              >
                <option value={NO_NOTE}>Don&apos;t write a note — just store and index</option>
                {styles.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.label}
                  </option>
                ))}
              </select>
            )}
          </Field>

          <Field
            label="Extra instruction (optional)"
            hint={
              style === NO_NOTE
                ? "On its own, this is the whole instruction for the note."
                : "Added to the style above, not instead of it."
            }
          >
            {(props) => (
              <textarea
                {...props}
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="e.g. focus on chapter 4, and keep the notation from the slides"
                rows={2}
                className={`${FIELD_CONTROL} resize-none`}
              />
            )}
          </Field>
        </div>

        <p className="mt-3 font-mono text-meta text-ink-faint">
          {!wantsNote || isLocal ? (
            <>Indexed on this machine — nothing leaves it.</>
          ) : (
            <>
              Indexed on this machine. Writing notes sends each file&apos;s text to{" "}
              <span className="text-warn">{modelName}</span>, which runs off it.
            </>
          )}
        </p>

        {error && (
          <p className="mt-3 font-mono text-meta text-danger" role="alert">
            {error}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <Button size="md" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" size="md" disabled={!picked.length || busy} onClick={run}>
            {busy ? "Starting…" : `Ingest ${picked.length || ""}`.trim()}
          </Button>
        </div>
      </Dialog>
      {confirmDialog}
    </>
  );
}
