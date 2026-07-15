"use client";

import { useEffect, useState } from "react";
import Panel from "@/components/Panel";
import { FLAGS } from "@/lib/flags";

interface LocalModel {
  name: string;
  endpoint: string;
}

const STORAGE_KEY = "argus-local-models";
const BUILT_IN = [
  { name: "claude-sonnet-4", tag: "API", isDefault: true },
  { name: "claude-haiku", tag: "API", isDefault: false },
];

function loadLocalModels(): LocalModel[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/**
 * MODELS (§7, §12) [PREVIEW] — registered-model list. Built-ins are static;
 * user-added local models persist to `localStorage["argus-local-models"]`
 * only (flags.localModels: preview) — routing an actual chat call to an
 * ollama-style endpoint isn't wired yet, so this is registration-UI-only, no
 * `fetch(` (§8 grep guard).
 */
export default function ModelsPanel() {
  const [models, setModels] = useState<LocalModel[]>([]);
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setModels(loadLocalModels());
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(models));
  }, [models, hydrated]);

  function addModel(event: React.FormEvent) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) return;
    setModels((current) => [...current, { name: trimmedName, endpoint: endpoint.trim() }]);
    setName("");
    setEndpoint("");
  }

  function removeModel(target: string) {
    setModels((current) => current.filter((model) => model.name !== target));
  }

  return (
    <Panel label="MODELS" preview={FLAGS.localModels === "preview"}>
      <ul className="space-y-1.5">
        {BUILT_IN.map((model) => (
          <li key={model.name} className="flex items-center gap-2.5 py-1">
            <span className="font-mono text-[13px] text-ink">{model.name}</span>
            <span className="border border-line px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-faint">
              {model.tag}
            </span>
            {model.isDefault && (
              <span className="border border-[var(--ac)] px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-[var(--ac)]">
                DEFAULT
              </span>
            )}
          </li>
        ))}
        {models.map((model) => (
          <li key={model.name} className="group flex items-center gap-2.5 py-1">
            <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-ink">{model.name}</span>
            <span className="border border-line px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-faint">
              LOCAL
            </span>
            {model.endpoint && <span className="truncate font-mono text-[10.5px] text-ink-faint">{model.endpoint}</span>}
            <button
              type="button"
              aria-label={`Remove ${model.name}`}
              onClick={() => removeModel(model.name)}
              className="hidden shrink-0 font-mono text-xs text-ink-faint hover:text-danger group-hover:inline"
            >
              ×
            </button>
          </li>
        ))}
      </ul>

      <form onSubmit={addModel} className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="model name (e.g. llama3.1)"
          className="min-w-0 flex-1 border border-line bg-sunken px-2.5 py-1.5 text-[12.5px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
        />
        <input
          value={endpoint}
          onChange={(e) => setEndpoint(e.target.value)}
          placeholder="http://localhost:11434/v1"
          className="min-w-0 flex-1 border border-line bg-sunken px-2.5 py-1.5 text-[12.5px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
        />
        <button
          type="submit"
          disabled={!name.trim()}
          className="shrink-0 border border-line px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
        >
          + ADD MODEL
        </button>
      </form>
    </Panel>
  );
}
