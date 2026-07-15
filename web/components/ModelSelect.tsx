"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useModels } from "@/lib/api";
import { BUILTIN_MODELS, setModel, useSelectedModel, type ModelEntry } from "@/lib/models";

/**
 * Model selector dropdown (§7). Registry comes from the real `GET
 * /api/models` (built-ins + user-registered local models, added on /system's
 * MODELS panel) via SWR, falling back to `BUILTIN_MODELS` while loading or if
 * the backend is unreachable. Selection persists via lib/models.ts and is
 * sent as the `model` field on every chat frame. Rendered on /chat's header;
 * the drawer header shows the selected name read-only.
 */
export default function ModelSelect() {
  const selected = useSelectedModel();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const { data } = useModels();

  const models: ModelEntry[] = data
    ? data.map((model) => ({
        name: model.name,
        kind: model.builtin ? "api" : "local",
        endpoint: model.endpoint ?? undefined,
      }))
    : BUILTIN_MODELS;

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onPointerDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onPointerDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Model: ${selected}`}
        onClick={() => setOpen((value) => !value)}
        className="border border-line bg-panel px-2.5 py-1.5 font-mono text-[11px] text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
      >
        {selected} ▾
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Models"
          className="animate-palette absolute right-0 top-full z-40 mt-1 w-60 border border-line bg-panel"
        >
          {models.map((entry) => {
            const active = entry.name === selected;
            return (
              <button
                key={entry.name}
                type="button"
                role="option"
                aria-selected={active}
                onClick={() => {
                  setModel(entry.name);
                  setOpen(false);
                }}
                className={`flex w-full items-center gap-2 border-b border-line px-3 py-2 text-left font-mono text-[11px] transition-colors hover:bg-[var(--ac-bg)] ${
                  active ? "text-[var(--ac)]" : "text-ink-muted"
                }`}
              >
                <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                <span className="border border-line px-1 py-px text-[8px] uppercase tracking-[0.14em] text-ink-faint">
                  {entry.kind === "local" ? "LOCAL" : "API"}
                </span>
                {active && <span aria-hidden>·</span>}
              </button>
            );
          })}
          <Link
            href="/system"
            onClick={() => setOpen(false)}
            className="block w-full px-3 py-2 text-left font-mono text-[11px] text-ink-faint transition-colors hover:bg-[var(--ac-bg)] hover:text-ink-muted"
          >
            + add local model
          </Link>
        </div>
      )}
    </div>
  );
}
