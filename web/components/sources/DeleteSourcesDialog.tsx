"use client";

import { useState } from "react";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import { deleteSources, type SourceInfo } from "@/lib/api";

/**
 * Confirming a delete of one or more sources.
 *
 * Built on `Dialog` rather than on `useConfirm`, which the rest of the app
 * uses for destructive prompts: that primitive collects free text, not a
 * choice, and cannot list the files being removed. Both are needed here —
 * naming what is about to go is the whole point of the confirmation, and the
 * generated note is a second decision the user has to be able to make.
 *
 * Initial focus lands on Cancel, matching `ConfirmDialog`, so Enter on a
 * destructive prompt backs out rather than committing.
 */
export default function DeleteSourcesDialog({
  sources,
  onClose,
  onDeleted,
}: {
  sources: SourceInfo[];
  onClose: () => void;
  onDeleted: (summary: { files: number; notes: number; chunks: number }) => void;
}) {
  // Ticked by default: a note Argus wrote from a lecture has little meaning
  // once the lecture is gone, and leaving it behind quietly grows a folder of
  // orphans. Ticked, not forced — it is still the user's file.
  const [includeGenerated, setIncludeGenerated] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Only a real source can have a note written *from* it; a generated note has
  // no companion of its own, so offering the choice for one would be noise.
  const canHaveNotes = sources.some((source) => source.generated === null);
  const many = sources.length !== 1;

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const summary = await deleteSources(
        sources.map((source) => source.path),
        canHaveNotes && includeGenerated,
      );
      onDeleted({
        files: summary.files_removed,
        notes: summary.notes_removed,
        chunks: summary.chunks_removed,
      });
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "the delete failed");
      setBusy(false);
    }
  }

  return (
    <Dialog
      label={many ? `Delete ${sources.length} sources` : "Delete source"}
      onClose={busy ? () => {} : onClose}
      className="w-[32rem] max-w-[calc(100vw-2rem)] p-5"
    >
      <p className="eyebrow mb-3">▍DELETE</p>

      <p className="text-body text-ink">
        Delete {many ? `these ${sources.length} files` : "this file"} from your vault?
      </p>
      <p className="mt-1 text-label text-ink-muted">
        {many ? "They are" : "It is"} removed from the search index too, so Argus stops answering
        from {many ? "them" : "it"} — a git snapshot makes this undoable.
      </p>

      <ul className="mt-3 max-h-40 overflow-y-auto border border-line px-3 py-2">
        {sources.map((source) => (
          <li key={source.path} className="truncate font-mono text-meta text-ink-muted">
            {source.path}
          </li>
        ))}
      </ul>

      {canHaveNotes && (
        <label className="mt-3 flex items-start gap-2 text-label text-ink-muted">
          <input
            type="checkbox"
            checked={includeGenerated}
            onChange={(event) => setIncludeGenerated(event.target.checked)}
            className="mt-0.5 accent-[var(--ac)]"
          />
          <span>
            Also delete the note Argus wrote from {many ? "each of them" : "it"}
            <span className="mt-0.5 block font-mono text-micro text-ink-muted">
              Only a note whose own frontmatter names {many ? "one of these files" : "this file"} as
              its source. Your own notes are never touched.
            </span>
          </span>
        </label>
      )}

      {error && <p className="mt-3 font-mono text-meta text-danger">{error}</p>}

      <div className="mt-5 flex items-center gap-2 border-t border-line pt-4">
        <Button size="md" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button size="md" variant="danger" onClick={run} disabled={busy} className="ml-auto">
          {busy ? "Deleting…" : "DELETE"}
        </Button>
      </div>
    </Dialog>
  );
}
