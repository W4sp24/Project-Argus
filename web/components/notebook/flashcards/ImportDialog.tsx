"use client";

import { useMemo, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import Dialog from "@/components/ui/Dialog";
import NotebookSegmented from "@/components/notebook/NotebookSegmented";
import { importFromNote, importPaste, useNotes } from "@/lib/api";
import { detectFormat, parseDelimited, parseQaPairs } from "@/lib/flashcards/parsing";

// Names, not characters: these are the wire values the server's parser knows,
// and GET /api/flashcards/import/delimiters is their source of truth.
const FIELDS = ["tab", "comma", "dash"] as const;
const FIELD_LABELS = { tab: "a tab", comma: "a comma", dash: "a dash" } as const;

const ROWS = ["newline", "semicolon"] as const;
const ROW_LABELS = { newline: "a new line", semicolon: "a semicolon" } as const;

type Field = (typeof FIELDS)[number];
type Row = (typeof ROWS)[number];
type Tab = "paste" | "file" | "note";

/** What a dropped or browsed file may be. Anything Argus can read as text. */
const ACCEPTED = [".md", ".txt", ".csv", ".tsv"];

/** Refuse a file that is clearly not a card list before reading it into memory. */
const MAX_FILE_BYTES = 2 * 1024 * 1024;

/** The dialog's two button shapes, declared once so the four call sites
 *  cannot drift. Plain buttons rather than `ui/Button`: that component is
 *  shared with every other mode and still wears the violet chrome. */
const QUIET =
  "rounded-ctl border border-nb-line px-4 py-2.5 text-ctl text-nb-body transition-colors hover:border-nb-lineHi hover:text-nb-ink";
const PRIMARY =
  "rounded-ctl bg-[var(--ac)] px-5 py-2.5 text-body font-semibold text-nb-onAc transition-opacity hover:opacity-90 disabled:opacity-50";

/** How many notes the picker shows before asking you to narrow the filter. */
const NOTE_CAP = 40;

/**
 * The three ways to fill a deck that do not involve a model.
 *
 * **Paste** — choose the two delimiters, paste, see what will be created
 * before you commit.
 *
 * **File** — drop a `.md`/`.txt`/`.csv`/`.tsv` from your computer. It is read
 * in the browser and its format is guessed; nothing is written to your vault
 * and no model is called. The zone is also a real file input, because a
 * drop-only target cannot be reached from a keyboard — and because that is
 * what makes the same code path testable.
 *
 * **From a note** — pick from the vault's notes. This used to be a text box
 * you typed a path into, which meant knowing the path, spelling it exactly,
 * with no listing and no completion. Reads `Q::`/`A::` pairs, which every note
 * Argus generates already carries in its self-test section.
 *
 * Every preview here runs `lib/flashcards/parsing.ts`, a deliberate twin of
 * the server's parser — a count shown before importing that differs from what
 * arrives is a lie, and two parsers is how that happens.
 */
export default function ImportDialog({
  deckId,
  deckTitle,
  course,
  onClose,
  onImported,
}: {
  deckId: number;
  /** Names the deck in the dialog's own title, so the accessible name says
   *  which deck the cards are going into rather than just "Import cards". */
  deckTitle?: string;
  /** Pre-narrows the note picker to the deck's own course, when it has one. */
  course?: string;
  onClose: () => void;
  onImported: () => void;
}) {
  const { show } = useToast();
  const [tab, setTab] = useState<Tab>("paste");
  const [text, setText] = useState("");
  const [field, setField] = useState<Field>("tab");
  const [row, setRow] = useState<Row>("newline");
  const [asQa, setAsQa] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [noteFilter, setNoteFilter] = useState(course ?? "");
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const { data: notes } = useNotes();

  const preview = useMemo(
    () => (asQa ? parseQaPairs(text) : parseDelimited(text, field, row)),
    [asQa, text, field, row],
  );

  const matches = useMemo(() => {
    const needle = noteFilter.trim().toLowerCase();
    const all = notes ?? [];
    if (!needle) return all;
    return all.filter(
      (note) =>
        note.title.toLowerCase().includes(needle) || note.path.toLowerCase().includes(needle),
    );
  }, [notes, noteFilter]);

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

  /**
   * Read one file and let its contents decide the format.
   *
   * Shared by the drop handler and the file input, so the accessible path and
   * the pointer path cannot behave differently.
   */
  async function takeFile(file: File | undefined) {
    if (!file) return;
    const name = file.name.toLowerCase();
    if (!ACCEPTED.some((ext) => name.endsWith(ext))) {
      show(`"${file.name}" isn't a text file — use ${ACCEPTED.join(", ")}`, { tone: "error" });
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      show(`"${file.name}" is too large (max 2 MB)`, { tone: "error" });
      return;
    }
    const body = await file.text();
    const detected = detectFormat(body);
    setText(body);
    setAsQa(detected.format === "qa");
    setField(detected.field as Field);
    setRow(detected.row as Row);
    setFileName(file.name);
    setTab("file");
    if (detected.cards.length === 0) {
      show(`nothing card-shaped in "${file.name}" — try another format below`, { tone: "error" });
    }
  }

  function importText() {
    return run(() =>
      importPaste(deckId, {
        text,
        field,
        row,
        format: asQa ? "qa" : "delimited",
      }),
    );
  }

  const previewBlock = (
    <>
      <div className="mt-3 flex flex-wrap items-end gap-4">
        <label className="flex items-center gap-2.5 text-ctl text-nb-body">
          <input
            type="checkbox"
            checked={asQa}
            onChange={(event) => setAsQa(event.target.checked)}
            className="h-3.5 w-3.5 accent-[var(--ac)]"
          />
          Q:: / A:: pairs
        </label>
        {!asQa && (
          <>
            <div>
              <p className="mb-1.5 text-ctl text-nb-body">Front and back are split by</p>
              <NotebookSegmented
                options={FIELDS}
                labels={FIELD_LABELS}
                value={field}
                onChange={setField}
              />
            </div>
            <div>
              <p className="mb-1.5 text-ctl text-nb-body">Cards are split by</p>
              <NotebookSegmented options={ROWS} labels={ROW_LABELS} value={row} onChange={setRow} />
            </div>
          </>
        )}
      </div>

      {preview.length === 0 ? (
        <p className="mt-4 text-body text-nb-faint" role="status">
          Nothing to import yet.
        </p>
      ) : (
        <div
          className="mt-4 rounded-tile border border-nb-okLine bg-nb-okBg p-3.5"
          role="status"
        >
          <p className="mb-2.5 text-ctl font-semibold text-ok">
            {preview.length} card{preview.length === 1 ? "" : "s"} will be added
          </p>
          <ul className="flex max-h-32 flex-col gap-1.5 overflow-auto">
            {preview.slice(0, 5).map((card, index) => (
              <li key={index} className="truncate text-ctl text-nb-body">
                {card.front} <span className="text-ok">→</span> {card.back}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 flex justify-end gap-2.5">
        <button type="button" onClick={onClose} className={QUIET}>
          Cancel
        </button>
        <button
          type="button"
          disabled={preview.length === 0 || busy}
          onClick={() => void importText()}
          className={PRIMARY}
        >
          Add {preview.length} card{preview.length === 1 ? "" : "s"}
        </button>
      </div>
    </>
  );

  return (
    <Dialog
      label={deckTitle ? `Add cards to ${deckTitle}` : "Import cards"}
      onClose={onClose}
      align="center"
      className="w-[min(44rem,92vw)] rounded-[0.875rem] !border-nb-lineHi !bg-nb-panel p-6"
    >
      {/* The whole dialog is a drop target, not just the FILE zone. Dropping
          onto whichever tab happens to be showing is what people actually do,
          and landing on a dead surface reads as "this app can't do that". */}
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragOver(false);
          void takeFile(event.dataTransfer.files?.[0]);
        }}
        className={dragOver ? "outline outline-1 outline-[var(--ac)]" : ""}
      >
        <p className="mb-5 text-body text-nb-body">
          Nothing here calls a model, and nothing is uploaded — a file is read right in this
          window.
        </p>

        <div role="tablist" aria-label="Import source" className="mb-4 flex gap-2">
          {(
            [
              ["paste", "Paste rows"],
              ["file", "Drop a file"],
              ["note", "From a note"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              onClick={() => setTab(value)}
              className={`flex-1 rounded-ctl border px-3 py-2.5 text-center text-ctl transition-colors ${
                tab === value
                  ? "border-[var(--ac)] bg-nb-acBg font-semibold text-[var(--ac)]"
                  : "border-nb-line bg-nb-raised text-nb-ink hover:border-nb-lineHi"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === "paste" && (
          <>
            <label className="block">
              <span className="sr-only">Paste rows</span>
              <textarea
                value={text}
                onChange={(event) => setText(event.target.value)}
                rows={8}
                aria-label="Paste rows"
                placeholder={"term\tdefinition\nanother\tone"}
                className="w-full rounded-tile border border-nb-line bg-nb-void p-3.5 font-mono text-ctl leading-relaxed text-nb-ink placeholder:text-nb-faint focus:border-nb-lineHi"
              />
            </label>
            {previewBlock}
          </>
        )}

        {tab === "file" && (
          <>
            {/* A label wrapping a hidden input: clickable, focusable, and
                announced — everything a bare div listening for `drop` is not. */}
            <label
              className={`flex min-h-32 cursor-pointer flex-col items-center justify-center gap-1.5 rounded-tile border border-dashed p-7 text-center transition-colors ${
                dragOver ? "border-[var(--ac)] bg-nb-acBg" : "border-nb-line hover:border-nb-lineHi"
              }`}
            >
              <input
                ref={fileInput}
                type="file"
                accept={ACCEPTED.join(",")}
                className="hidden"
                onChange={(event) => {
                  void takeFile(event.target.files?.[0]);
                  event.target.value = "";
                }}
              />
              <span className="text-body font-medium text-nb-ink">
                {fileName ?? "Drop a file, or click to browse"}
              </span>
              <span className="text-label text-nb-faint">
                {ACCEPTED.join(" · ")} — read here, never uploaded
              </span>
            </label>
            {text ? (
              previewBlock
            ) : (
              <div className="mt-4 flex justify-end">
                <button type="button" onClick={onClose} className={QUIET}>
                  Cancel
                </button>
              </div>
            )}
          </>
        )}

        {tab === "note" && (
          <>
            <label className="block">
              <span className="mb-1.5 block text-ctl text-nb-body">Search your notes</span>
              <input
                type="search"
                value={noteFilter}
                onChange={(event) => setNoteFilter(event.target.value)}
                placeholder="title or path"
                className="min-h-10 w-full rounded-ctl border border-nb-line bg-nb-void px-3.5 py-2.5 text-body text-nb-ink placeholder:text-nb-faint focus:border-nb-lineHi"
              />
            </label>

            {!notes ? (
              <p className="mt-3 text-label text-nb-faint">Loading notes…</p>
            ) : matches.length === 0 ? (
              <p className="mt-3 text-label text-nb-faint">
                {notes.length === 0
                  ? "No notes in the vault yet."
                  : `Nothing matches “${noteFilter}”.`}
              </p>
            ) : (
              <ul className="mt-3 flex max-h-64 flex-col gap-1 overflow-auto rounded-tile border border-nb-line p-2">
                {matches.slice(0, NOTE_CAP).map((note) => (
                  <li key={note.path}>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void run(() => importFromNote(deckId, note.path))}
                      className="flex w-full flex-col items-start rounded-ctl px-3 py-2 text-left transition-colors hover:bg-nb-raised disabled:opacity-70"
                    >
                      <span className="w-full truncate text-body text-nb-ink">{note.title}</span>
                      <span className="w-full truncate text-label text-nb-faint">
                        {note.path}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {matches.length > NOTE_CAP && (
              <p className="mt-1 text-label text-nb-faint">
                {NOTE_CAP} of {matches.length} — narrow the search to see the rest
              </p>
            )}

            <p className="mt-2 text-label text-nb-faint">
              Reads every <code className="font-mono">Q::</code> /{" "}
              <code className="font-mono">A::</code> pair in the note. Notes Argus generates carry
              them in their self-test section, so a lecture note usually works as-is.
            </p>
            <div className="mt-4 flex justify-end">
              <button type="button" onClick={onClose} className={QUIET}>
                Cancel
              </button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
