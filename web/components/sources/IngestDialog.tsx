"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import { useConfirm } from "@/components/ui/useConfirm";
import {
  ApiError,
  HASH_MAX_BYTES,
  INGEST_ACCEPT,
  INGEST_MAX_FILES,
  INGEST_MAX_FILE_BYTES,
  INGEST_SUFFIXES,
  formatBytes,
  hashFile,
  ingestRejection,
  pooledEach,
  precheckIngest,
  startIngestJob,
  useIngestDestinations,
  useIngestNoteStyles,
  useModels,
  type HashResult,
  type HashSkip,
} from "@/lib/api";
import { useSelectedModel } from "@/lib/models";

/** The "don't write anything" option. Not a style key the backend knows. */
const NO_NOTE = "";

/**
 * How many files are prechecked at once.
 *
 * Each unit is one `POST /ingest/precheck` *and* one full read of the file to
 * hash it, so the batch used to run under a single `Promise.all`: fifty 30MB
 * lecture PDFs meant ~1.5GB resident on the main thread before a byte had been
 * uploaded. Four in flight keeps the warning fast without holding the batch.
 */
const PRECHECK_CONCURRENCY = 4;

/** Why a file's verdict is less certain than it looks. */
type Caveat = HashSkip | "precheck-failed";

/** What the precheck found about one picked file, once it has answered. */
interface Collision {
  /** The picked file's name already exists at the destination. */
  exists: boolean;
  /** …and holds exactly these bytes, so re-adding it changes nothing. */
  identical: boolean;
  /**
   * Set when `identical` is a guess rather than a comparison — the contents
   * could not be hashed here, or the precheck itself never answered. `false`
   * with a caveat means "unknown", and the UI has to say so instead of
   * quietly reporting a second copy.
   */
  caveat: Caveat | null;
}

interface Picked {
  file: File;
  /** Undefined until the precheck for the *current* destination has answered. */
  collision?: Collision;
}

/**
 * The destination we can legally write to that is closest to `folder`.
 *
 * `folder` comes from the FOLDERS rail, so it is any vault path at all, while
 * `options` is the taxonomy-derived list the backend serves
 * (`GET /api/ingest/destinations`). Filtering to a course root and hitting
 * ingest should land in that course rather than silently in the inbox, so a
 * folder that contains a destination -- or sits inside one -- snaps to it.
 *
 * Everything is derived from `options`; no zone name is hard-coded here. The
 * list is taxonomy-driven and grows with every course, so anything that
 * assumed its length or its contents would be wrong on the next vault.
 */
export function nearestDestination(folder: string, options: string[]): string {
  const clean = folder.replace(/\/+$/, "");
  if (!clean || !options.length) return options[0] ?? "";
  if (options.includes(clean)) return clean;

  // Segment-aware, so `15-Courses/CS0` is not treated as a parent of
  // `15-Courses/CS000`. Longest wins: the most specific legal ancestor.
  const isUnder = (child: string, parent: string) => child.startsWith(`${parent}/`);
  const ancestors = options.filter((option) => isUnder(clean, option));
  if (ancestors.length) {
    return ancestors.reduce((best, option) => (option.length > best.length ? option : best));
  }

  // Otherwise the folder may be a parent of several destinations -- a course
  // root sits above both its `materials` and its `notes` zone. Take the first
  // in the backend's own order, which lists a course's materials before its
  // notes; picking the shortest instead would land lectures in `notes/`,
  // where the Course Hub's materials listing never counts them.
  const descendant = options.find((option) => isUnder(option, clean));
  if (descendant) return descendant;

  return options[0];
}

function collisionNote(picked: Picked, replace: boolean): string | null {
  if (!picked.collision?.exists) return null;
  if (picked.collision.identical) {
    return replace ? "already ingested — will be rewritten" : "already ingested — will be copied";
  }
  return replace ? "replaces the copy in your vault" : "saved alongside as a second copy";
}

/**
 * The one sentence that says a verdict was reached with less than full
 * information. Silence here is what made friction 2 invisible: outside a
 * secure context `crypto.subtle` is undefined, every digest came back null,
 * and so every re-ingest of a byte-identical file confidently reported
 * "saved alongside as a second copy".
 */
function caveatNote(caveat: Caveat): string {
  switch (caveat) {
    case "insecure-context":
      return (
        "This page is not on a secure origin, so file contents cannot be compared here — " +
        "a name that already exists reads as changed even when the bytes are identical."
      );
    case "too-large":
      return (
        `Files over ${formatBytes(HASH_MAX_BYTES)} are matched by name only, so a re-ingest ` +
        "of an unchanged one still reads as a second copy."
      );
    case "unavailable":
      return (
        "This browser could not read the files to compare them, so a name that already " +
        "exists reads as changed even when the bytes are identical."
      );
    case "precheck-failed":
      return (
        "Argus could not be asked what is already at this destination — a collision will " +
        "only be discovered after the upload."
      );
  }
}

export default function IngestDialog({
  onClose,
  onStarted,
  initialTarget,
  lockedTarget,
  defaultNoteStyle,
  initialFiles,
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
  /** Files to open with, so a drop on a dropzone elsewhere carries straight
   * into the dialog instead of being discarded and re-asked for. */
  initialFiles?: File[];
}) {
  const { data: destinations } = useIngestDestinations();
  const { data: noteStyles } = useIngestNoteStyles();
  const { data: models } = useModels();
  const modelName = useSelectedModel();
  const { confirm, confirmDialog } = useConfirm();

  // Seeded rather than added through `add()`: a drop on a dropzone elsewhere
  // has already chosen the files, and the collision precheck for them runs
  // once the destination settles rather than at mount, when `options` may not
  // have arrived yet.
  const [picked, setPicked] = useState<Picked[]>(() =>
    (initialFiles ?? []).map((file) => ({ file })),
  );
  const [target, setTarget] = useState(lockedTarget ?? initialTarget ?? "");
  const [style, setStyle] = useState(defaultNoteStyle ?? NO_NOTE);
  const [prompt, setPrompt] = useState("");
  const [replace, setReplace] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  /** 0..1 while the upload streams, `null` when the browser will not say. */
  const [uploaded, setUploaded] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLButtonElement>(null);

  // Memoised because it is an effect dependency: a fresh [] every render
  // would re-run the default-target effect on every keystroke.
  const options = useMemo(() => destinations?.destinations ?? [], [destinations]);
  useEffect(() => {
    // Two ways this used to end up POSTing somewhere the user was never shown:
    //
    // 1. `initialTarget` is whatever folder /sources was filtered to, which is
    //    an arbitrary vault path and usually not a registered destination. The
    //    old guard bailed on any truthy `target`, so it was never checked
    //    against `options`. A <select> with no matching <option> falls back to
    //    displaying index 0 while React state keeps the unlisted value, and
    //    nothing reconciles them -- the dialog said `00-Inbox/files` and wrote
    //    to the course root.
    // 2. `lockedTarget` arrives from `GET /api/study/courses`, which can
    //    resolve *after* the dialog mounts. It only ever seeded useState, and
    //    the guard returned early whenever it was set, so a dialog opened a
    //    moment too early kept the default inbox, hid the picker because it
    //    was now "locked", and ingested outside the course.
    //
    // So: never return without knowing `target` is a value the user can see.
    if (lockedTarget) {
      if (target !== lockedTarget) setTarget(lockedTarget);
      return;
    }
    if (!options.length || options.includes(target)) return;
    setTarget(nearestDestination(target, options));
  }, [lockedTarget, options, target]);

  const destination = target || options[0] || "";

  /**
   * Which destination each file's verdict belongs to. A `WeakMap` so removing
   * a file from the batch drops its bookkeeping with it, and so this cannot
   * become a second, divergent record of what is picked.
   */
  const checkedFor = useRef(new WeakMap<File, string>());
  /** Digests, kept across destination changes: contents do not change, and
   * re-hashing a fifty-file batch on every flick of the picker is the memory
   * spike this pass exists to remove. */
  const digests = useRef(new WeakMap<File, HashResult>());

  // The precheck lives here, keyed on the destination as well as the files,
  // because it used to run once at pick time against `target || options[0]`.
  // Changing "Save to" afterwards left the collision notes describing the old
  // destination -- and `collides`, the sole gate on whether the Replace
  // checkbox renders at all, is computed from them. Picking files where they
  // do not collide and then switching to a folder where they do meant the
  // checkbox never appeared and the duplicates were written silently.
  //
  // It is also what covers `initialFiles`, which are seeded straight into
  // `picked` and never pass through `add()`.
  useEffect(() => {
    if (!destination) return;
    const stale = picked.filter((entry) => checkedFor.current.get(entry.file) !== destination);
    if (!stale.length) return;

    const files = stale.map((entry) => entry.file);
    // Claimed before the first await, so the re-render this effect causes does
    // not queue the same precheck a second time.
    for (const file of files) checkedFor.current.set(file, destination);

    // The previous destination's verdict is wrong the instant the destination
    // changes; leaving it on screen until the new one answers is the stale
    // warning in miniature.
    if (stale.some((entry) => entry.collision)) {
      const dropping = new Set(files);
      setPicked((current) =>
        current.map((entry) => (dropping.has(entry.file) ? { file: entry.file } : entry)),
      );
    }

    void pooledEach(files, PRECHECK_CONCURRENCY, async (file) => {
      let collision: Collision;
      try {
        const hashed = digests.current.get(file) ?? (await hashFile(file));
        digests.current.set(file, hashed);
        const found = await precheckIngest(file.name, destination);
        collision = {
          exists: found.exists,
          identical: Boolean(found.sha256 && hashed.digest && found.sha256 === hashed.digest),
          caveat: found.exists ? hashed.skipped : null,
        };
      } catch {
        // A precheck that cannot answer must not block the ingest; the user
        // loses the advance warning, and is told that they have.
        collision = { exists: false, identical: false, caveat: "precheck-failed" };
      }
      // The destination may have changed again while this was in flight, in
      // which case this answer is already the stale one.
      if (checkedFor.current.get(file) !== destination) return;
      setPicked((current) =>
        current.map((entry) => (entry.file === file ? { ...entry, collision } : entry)),
      );
    });
  }, [destination, picked]);

  // The badge is the honest half of the trust line: a hosted model means the
  // file's text leaves this machine, and the panel has promised the opposite
  // since it shipped. `local` comes from the registry, not from `builtin` — a
  // self-hosted open-weight endpoint is still a network hop.
  const isLocal = models?.find((model) => model.name === modelName)?.local ?? false;
  // The style select is the whole answer. It used to be `style !== NO_NOTE ||
  // prompt.trim().length > 0`, so typing a note-to-self with "Don't write a
  // note" selected wrote one anyway and sent every file's text to a hosted
  // provider — a control and its own helper text contradicting each other on
  // screen, with the behaviour following the helper text. "Don't" means don't;
  // the instruction field is disabled instead of quietly overriding it.
  const wantsNote = style !== NO_NOTE;

  function add(files: FileList | File[] | null) {
    if (!files) return;
    const incoming = Array.from(files);
    if (!incoming.length) return;

    // Both paths validate here, not just the picker: `accept` constrains the
    // file dialog and nothing else, so a dropped `.zip` was queued, hashed,
    // uploaded and only rejected server-side after the wait.
    const room = INGEST_MAX_FILES - picked.length;
    const accepted: File[] = [];
    const refused: string[] = [];
    let overflow = 0;
    for (const file of incoming) {
      const rejection = ingestRejection(file);
      if (rejection) {
        refused.push(rejection);
        continue;
      }
      // The cap used to be a silent `slice()` followed by `setError(null)` on
      // the next line, so dropping sixty files when fifty were queued
      // discarded ten and cleared the only message that could have said so.
      if (accepted.length >= room) {
        overflow += 1;
        continue;
      }
      accepted.push(file);
    }
    if (overflow) {
      refused.push(
        `${overflow} more file${overflow === 1 ? " was" : "s were"} not added — ` +
          `${INGEST_MAX_FILES} at once is the limit`,
      );
    }

    setError(refused.length ? refused.join(" · ") : null);
    if (accepted.length) {
      setPicked((current) => [...current, ...accepted.map((file) => ({ file }))]);
    }
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
    setUploaded(0);
    setError(null);
    try {
      const jobId = await startIngestJob(
        picked.map((entry) => entry.file),
        {
          target,
          noteStyle: style,
          // Never sent when no note was asked for. The field is disabled in
          // that state, but text typed before switching the style back to
          // "Don't write a note" would otherwise still ride along.
          summaryPrompt: wantsNote ? prompt.trim() : "",
          replace,
        },
        setUploaded,
      );
      onStarted(jobId);
      onClose();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Could not reach Argus. Is the backend running?",
      );
      setBusy(false);
      setUploaded(null);
    }
  }

  /**
   * Escape and a backdrop click, which `Dialog` treats as the same gesture.
   *
   * Forty files, a destination, a style and a typed instruction were one
   * keystroke from gone: `picked` is component state and nothing about it is
   * recoverable. Cancel is deliberately *not* routed through here — a button
   * labelled Cancel is a decision, Escape is frequently a reflex.
   */
  async function requestDismiss() {
    // An upload is in flight; Cancel is disabled for the same reason.
    if (busy) return;
    if (picked.length > 0) {
      const answer = await confirm({
        label: "Discard these files",
        message: `Closing drops the ${picked.length} file${
          picked.length === 1 ? "" : "s"
        } you have queued here.`,
        detail: "Nothing has been uploaded yet, so they would have to be picked again.",
        confirmLabel: "Discard",
        cancelLabel: "Keep them",
      });
      if (answer === null) return;
    }
    onClose();
  }

  const collides = picked.some((entry) => entry.collision?.exists);
  /** No verdict yet — the precheck for this destination is still in flight. */
  const checking = picked.some((entry) => !entry.collision);
  const caveat = picked.find((entry) => entry.collision?.caveat)?.collision?.caveat ?? null;
  const styles = noteStyles?.styles ?? [];
  // The chosen style's own description, so the hint says what this note will
  // actually contain instead of restating that a note gets written.
  const styleHint =
    styles.find((option) => option.key === style)?.description ??
    "Saved as markdown beside the file — or in the course's notes/ folder — and indexed too.";
  const uploadPct = uploaded === null ? null : Math.round(uploaded * 100);

  return (
    <>
      <Dialog
        label="Ingest files"
        onClose={() => void requestDismiss()}
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
            add(event.dataTransfer.files);
          }}
          onClick={() => inputRef.current?.click()}
          className={`w-full border border-dashed px-4 py-6 text-center transition-[border-color,background-color] ${
            dragOver ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
          }`}
        >
          <span className="block font-mono text-label text-ink-muted">
            drop files, or click to choose
          </span>
          {/* The size limit is on the caption because it was nowhere at all:
              the only way to learn about it was a 413 after the upload. */}
          <span className="mt-1 block font-mono text-meta text-ink-faint">
            {INGEST_SUFFIXES.join(" ")} · up to {INGEST_MAX_FILES} at once ·{" "}
            {formatBytes(INGEST_MAX_FILE_BYTES)} each
          </span>
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={INGEST_ACCEPT}
          aria-hidden
          tabIndex={-1}
          className="hidden"
          onChange={(event) => add(event.target.files)}
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

          {/* Space reserved, not conditionally occupied. The precheck answers
              asynchronously, so the Replace checkbox used to appear in the
              middle of the dialog after the user had started reading it and
              shove everything below it down. The slot is the same height
              whether the answer is a checkbox or a sentence, and it says what
              it is waiting for rather than sitting blank. */}
          {picked.length > 0 && (
            <div className="min-h-[4.5rem]">
              {collides ? (
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
                      Without this, both copies stay indexed and an answer can cite either. Your
                      vault is snapshotted first, so a replace is one `git revert` away.
                    </span>
                  </span>
                </label>
              ) : (
                <p className="font-mono text-meta text-ink-faint">
                  {checking
                    ? `Checking what ${destination || "the destination"} already holds…`
                    : `Nothing in ${destination || "the destination"} shares these names.`}
                </p>
              )}
              {caveat && (
                <p className="mt-1 font-mono text-meta text-warn">{caveatNote(caveat)}</p>
              )}
            </div>
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
              wantsNote
                ? "Added to the style above, not instead of it."
                : "Choose a note style to add an instruction."
            }
          >
            {(props) => (
              <textarea
                {...props}
                value={prompt}
                disabled={!wantsNote}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="e.g. focus on chapter 4, and keep the notation from the slides"
                rows={2}
                className={`${FIELD_CONTROL} resize-none disabled:cursor-not-allowed disabled:opacity-50`}
              />
            )}
          </Field>
        </div>

        {/* `text-ink-muted`, not the `text-ink-faint` the rest of this dialog's
            chrome uses: this is the sentence a user reads to decide whether to
            hand a file's text to a hosted model. A privacy claim must never be
            the faintest thing on the screen it appears on. */}
        <p className="mt-3 font-mono text-meta text-ink-muted">
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

        {/* The upload is the part with no instrumentation: one POST carrying
            up to fifty files, and until the 202 comes back the only signal was
            a button reading "Starting…". Tens of seconds of dead UI on a large
            batch, well past the 300ms where a person expects movement. */}
        {busy && uploadPct !== null && (
          <div
            className="mt-3 h-1 w-full bg-sunken"
            role="progressbar"
            aria-label="Upload progress"
            aria-valuenow={uploadPct}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full bg-[var(--ac)] transition-[width]"
              style={{ width: `${uploadPct}%` }}
            />
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          {/* Disabled while busy: the upload is already in flight and closing
              the dialog cancels nothing, it only hides the job from the person
              who started it. */}
          <Button size="md" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" size="md" disabled={!picked.length || busy} onClick={run}>
            {busy
              ? uploadPct === null || uploadPct >= 100
                ? "Starting…"
                : `Uploading ${uploadPct}%`
              : `Ingest ${picked.length || ""}`.trim()}
          </Button>
        </div>
      </Dialog>
      {confirmDialog}
    </>
  );
}
