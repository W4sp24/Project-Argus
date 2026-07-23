"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { createQuickLink, deleteQuickLink, updateQuickLink, useQuickLinks, type QuickLink } from "@/lib/api";
import { FLAGS } from "@/lib/flags";
import { isValidUrl, openExternalUrl } from "@/lib/quickLinks";

const PRESET_ICONS = ["★", "◆", "⬡", "▲", "●", "⌂", "✉", "⚙", "↗"];

/**
 * QUICK.LINKS (§ dashboard) — pinned launch links, backed for real by
 * `/api/quick-links` (see `web/lib/api.ts` / `web/lib/quickLinks.ts`).
 * Mirrors TasksPanel's add-form + hover-revealed-row-controls + inline-edit
 * conventions.
 */
export default function QuickLinks() {
  const { data, mutate, isLoading } = useQuickLinks();
  const links = data ?? [];
  const { show } = useToast();

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editIcon, setEditIcon] = useState("");
  const [editLabel, setEditLabel] = useState("");
  const [editUrl, setEditUrl] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

  const [addIcon, setAddIcon] = useState("");
  const [addLabel, setAddLabel] = useState("");
  const [addUrl, setAddUrl] = useState("");
  const [adding, setAdding] = useState(false);

  function startEdit(link: QuickLink) {
    setEditingId(link.id);
    setEditIcon(link.icon ?? "");
    setEditLabel(link.label);
    setEditUrl(link.url);
  }

  function cancelEdit() {
    setEditingId(null);
  }

  async function saveEdit(link: QuickLink) {
    if (savingEdit) return;
    const label = editLabel.trim();
    if (!label || !isValidUrl(editUrl)) {
      show("quick-links :: enter a label and a valid https URL");
      return;
    }
    setSavingEdit(true);
    try {
      await updateQuickLink(link.id, { label, url: editUrl, icon: editIcon.trim() || null });
      setEditingId(null);
      show(`quick-links :: saved ${label}`);
    } catch (error) {
      show(error instanceof Error ? error.message : "Save failed");
    } finally {
      setSavingEdit(false);
      mutate();
    }
  }

  async function remove(link: QuickLink) {
    if (!window.confirm(`Delete "${link.label}"?`)) return;
    try {
      await deleteQuickLink(link.id);
      show(`quick-links :: deleted ${link.label}`);
    } catch (error) {
      show(error instanceof Error ? error.message : "Delete failed");
    }
    mutate();
  }

  async function move(index: number, direction: -1 | 1) {
    const other = index + direction;
    if (other < 0 || other >= links.length) return;
    const a = links[index];
    const b = links[other];
    const optimistic = links.slice();
    optimistic[index] = b;
    optimistic[other] = a;
    mutate(optimistic, false);
    try {
      await Promise.all([
        updateQuickLink(a.id, { sort_order: b.sort_order }),
        updateQuickLink(b.id, { sort_order: a.sort_order }),
      ]);
    } catch (error) {
      show(error instanceof Error ? error.message : "Reorder failed");
    }
    mutate();
  }

  async function submitAdd(event: React.FormEvent) {
    event.preventDefault();
    if (adding) return;
    const label = addLabel.trim();
    if (!label || !isValidUrl(addUrl)) {
      show("quick-links :: enter a label and a valid https URL");
      return;
    }
    setAdding(true);
    try {
      await createQuickLink({ label, url: addUrl, icon: addIcon.trim() || null });
      setAddIcon("");
      setAddLabel("");
      setAddUrl("");
      show(`quick-links :: added ${label}`);
    } catch (error) {
      show(error instanceof Error ? error.message : "Add failed");
    } finally {
      setAdding(false);
      mutate();
    }
  }

  return (
    <Panel label="QUICK.LINKS" preview={(FLAGS.quickLinks as string) === "preview"}>
      <form
        onSubmit={submitAdd}
        className="mb-4 flex items-center gap-2 border border-line px-3 py-2 focus-within:border-lineHi"
      >
        <span className="shrink-0 font-mono text-[var(--ac)]">＋</span>
        <input
          value={addIcon}
          onChange={(event) => setAddIcon(event.target.value)}
          placeholder="↗"
          aria-label="Icon glyph"
          className="w-10 shrink-0 bg-transparent text-center text-[13.5px] placeholder:text-ink-faint focus:outline-none"
        />
        <input
          value={addLabel}
          onChange={(event) => setAddLabel(event.target.value)}
          placeholder="label"
          aria-label="Link label"
          className="min-w-0 flex-[0.8] bg-transparent text-[13.5px] placeholder:text-ink-faint focus:outline-none"
        />
        <input
          value={addUrl}
          onChange={(event) => setAddUrl(event.target.value)}
          placeholder="https://…"
          aria-label="Link URL"
          className="min-w-0 flex-1 bg-transparent text-[13.5px] placeholder:text-ink-faint focus:outline-none"
        />
        <button
          type="submit"
          disabled={adding}
          className="shrink-0 border border-line bg-[var(--ac-bg)] px-2 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--ac)] transition-colors hover:border-lineHi disabled:opacity-40"
        >
          ADD
        </button>
      </form>

      <div className="mb-3 flex items-center gap-1.5">
        {PRESET_ICONS.map((glyph) => (
          <button
            key={glyph}
            type="button"
            onClick={() => setAddIcon(glyph)}
            aria-label={`Use ${glyph} icon`}
            className="font-mono text-xs text-ink-faint hover:text-[var(--ac)]"
          >
            {glyph}
          </button>
        ))}
      </div>

      {isLoading ? (
        <p className="py-1 text-[12px] text-ink-faint">loading…</p>
      ) : links.length === 0 ? (
        <p className="py-1 text-[12px] text-ink-faint">nothing here</p>
      ) : (
        <ul>
          {links.map((link, index) => {
            const editing = editingId === link.id;
            return (
              <li key={link.id} className="group flex items-center gap-2.5 py-1.5">
                {editing ? (
                  <form
                    className="flex min-w-0 flex-1 items-center gap-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      saveEdit(link);
                    }}
                  >
                    <input
                      value={editIcon}
                      onChange={(event) => setEditIcon(event.target.value)}
                      aria-label="Icon glyph"
                      className="w-8 shrink-0 border border-lineHi bg-sunken px-1 py-1 text-center text-[13.5px] focus:outline-none"
                    />
                    <input
                      autoFocus
                      value={editLabel}
                      onChange={(event) => setEditLabel(event.target.value)}
                      aria-label="Link label"
                      className="min-w-0 flex-[0.8] border border-lineHi bg-sunken px-2 py-1 text-[13.5px] focus:outline-none"
                    />
                    <input
                      value={editUrl}
                      onChange={(event) => setEditUrl(event.target.value)}
                      aria-label="Link URL"
                      className="min-w-0 flex-1 border border-lineHi bg-sunken px-2 py-1 text-[13.5px] focus:outline-none"
                    />
                    <button
                      type="submit"
                      disabled={savingEdit}
                      className="shrink-0 border border-line bg-[var(--ac-bg)] px-2 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--ac)] disabled:opacity-40"
                    >
                      SAVE
                    </button>
                    <button
                      type="button"
                      onClick={cancelEdit}
                      className="shrink-0 font-mono text-[10px] uppercase tracking-[0.12em] text-ink-faint hover:text-ink"
                    >
                      CANCEL
                    </button>
                  </form>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => openExternalUrl(link.url)}
                      title={link.url}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left text-[13.5px] text-ink transition-colors hover:text-ink-bright"
                    >
                      <span className="shrink-0 font-mono text-ink-faint">{link.icon || "↗"}</span>
                      <span className="min-w-0 truncate">{link.label}</span>
                    </button>
                    <span className="hidden shrink-0 items-center gap-2 group-hover:flex">
                      <button
                        aria-label="Move up"
                        disabled={index === 0}
                        onClick={() => move(index, -1)}
                        className="font-mono text-[10px] text-ink-faint hover:text-[var(--ac)] disabled:opacity-30"
                      >
                        ▲
                      </button>
                      <button
                        aria-label="Move down"
                        disabled={index === links.length - 1}
                        onClick={() => move(index, 1)}
                        className="font-mono text-[10px] text-ink-faint hover:text-[var(--ac)] disabled:opacity-30"
                      >
                        ▼
                      </button>
                      <button
                        aria-label="Edit link"
                        onClick={() => startEdit(link)}
                        className="font-mono text-[10px] text-ink-faint hover:text-[var(--ac)]"
                      >
                        edit
                      </button>
                      <button
                        aria-label="Delete link"
                        onClick={() => remove(link)}
                        className="font-mono text-xs text-ink-faint hover:text-danger"
                      >
                        ×
                      </button>
                    </span>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
