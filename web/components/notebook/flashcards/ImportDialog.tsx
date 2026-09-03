"use client";

import { useMemo, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import SegmentedControl from "@/components/ui/SegmentedControl";
import { importFromNote, importPaste } from "@/lib/api";
import { parseDelimited } from "@/lib/flashcards/parsing";

// Names, not characters: these are the wire values the server's parser knows,
// and GET /api/flashcards/import/delimiters is their source of truth.
const FIELDS = ["tab", "comma", "dash"] as const;
const FIELD_LABELS = { tab: "TAB", comma: "COMMA", dash: "DASH" } as const;

const ROWS = ["newline", "semicolon"] as const;
const ROW_LABELS = { newline: "NEW LINE", semicolon: "SEMICOLON" } as const;

type Field = (typeof FIELDS)[number];
type Row = (typeof ROWS)[number];

/**
 * The two ways to fill a deck that are neither typing nor a model.
 *
 * **Paste** is the affordance every flashcard tool offers: choose the two
 * delimiters, paste, see what will be created before you commit. The preview
 * runs `lib/flashcards/parsing.ts`, which is a deliberate twin of the server's
 * parser — the count shown is the count you get.
 *
 * **From a note** reads `Q::`/`A::` out of any vault note. That is the fix for
 * the whole feature having been unreachable: ingest writes a self-test tail in
 * exactly this shape into every note it generates, and until now nothing could
 * read them.
 */
export default function ImportDialog({
  deckId,
  onClose,
  onImported,
}: {
  deckId: number;
  onClose: () => void;
  onImported: () => void;
}) {
  const { show } = useToast();
  const [tab, setTab] = useState<"paste" | "note">("paste");
  const [text, setText] = useState("");
  const [field, setField] = useState<Field>("tab");
  const [row, setRow] = useState<Row>("newline");
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const firstField = useRef<HTMLTextAreaElement>(null);

  const preview = useMemo(() => parseDelimited(text, field, row), [text, field, row]);

  async function run(action: () => Promise<{ added: number }>) {
    setBusy(true);
    try {
      const { added } = await action();
      show(`imported :: ${added} card${added === 1 ? "" : "s"}`);
      onImported();
      onClose();
    } catch (error) {
      show(`import failed: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog label="Import cards" onClose={onClose} align="center" className="w-[min(44rem,92vw)] p-5">
      <div role="tablist" aria-label="Import source" className="mb-4 flex border border-line">
        {(["paste", "note"] as const).map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            onClick={() => setTab(value)}
            className={`border-r border-line px-3 py-2 font-mono text-label uppercase tracking-[0.12em] transition-colors last:border-r-0 ${
              tab === value
                ? "bg-[var(--ac-bg)] text-[var(--ac)] shadow-[inset_0_-2px_0_var(--ac)]"
                : "text-ink-faint hover:text-ink-muted"
            }`}
          >
            {value === "paste" ? "PASTE ROWS" : "FROM A NOTE"}
          </button>
        ))}
      </div>

      {tab === "paste" ? (
        <>
          <label className="block">
            <span className="mb-1 block font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
              Paste rows
            </span>
            <textarea
              ref={firstField}
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={8}
              placeholder={"term\tdefinition\nanother\tone"}
              className="w-full border border-line bg-sunken px-2 py-1.5 font-mono text-label text-ink focus:border-lineHi"
            />
          </label>

          <div className="mt-3 flex flex-wrap gap-4">
            <div>
              <p className="mb-1 font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
                Between front and back
              </p>
              <SegmentedControl
                options={FIELDS}
                labels={FIELD_LABELS}
                value={field}
                onChange={setField}
              />
            </div>
            <div>
              <p className="mb-1 font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
                Between cards
              </p>
              <SegmentedControl
                options={ROWS}
                labels={ROW_LABELS}
                value={row}
                onChange={setRow}
              />
            </div>
          </div>

          <p className="mt-3 font-mono text-label text-ink-muted" role="status">
            {preview.length === 0
              ? "nothing to import yet"
              : `${preview.length} card${preview.length === 1 ? "" : "s"} will be added`}
          </p>
          {preview.length > 0 && (
            <ul className="mt-2 max-h-32 space-y-1 overflow-auto border border-line p-2">
              {preview.slice(0, 5).map((card, index) => (
                <li key={index} className="truncate font-mono text-meta text-ink-faint">
                  {card.front} <span className="text-[var(--ac)]">→</span> {card.back}
                </li>
              ))}
            </ul>
          )}

          <div className="mt-4 flex justify-end gap-2">
            <Button variant="quiet" onClick={onClose}>
              CANCEL
            </Button>
            <Button
              disabled={preview.length === 0 || busy}
              onClick={() => void run(() => importPaste(deckId, { text, field, row }))}
            >
              IMPORT {preview.length}
            </Button>
          </div>
        </>
      ) : (
        <>
          <label className="block">
            <span className="mb-1 block font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
              Note path
            </span>
            <input
              value={path}
              onChange={(event) => setPath(event.target.value)}
              placeholder="15-Courses/CS201/flashcards.md"
              className="min-h-9 w-full border border-line bg-sunken px-2 py-1.5 font-mono text-label text-ink focus:border-lineHi"
            />
          </label>
          <p className="mt-2 text-label text-ink-faint">
            Reads every <code className="font-mono">Q::</code> /{" "}
            <code className="font-mono">A::</code> pair in the note. Notes Argus generates carry
            them in their self-test section, so a lecture note usually works as-is.
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="quiet" onClick={onClose}>
              CANCEL
            </Button>
            <Button
              disabled={!path.trim() || busy}
              onClick={() => void run(() => importFromNote(deckId, path.trim()))}
            >
              IMPORT
            </Button>
          </div>
        </>
      )}
    </Dialog>
  );
}
