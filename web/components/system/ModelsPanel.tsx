"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { ApiError, mutateJSON, useModels } from "@/lib/api";
import { FLAGS } from "@/lib/flags";

/**
 * MODELS (§7, §12) — wired to the real registry: `GET /api/models` (built-ins
 * + user-added local models), `POST /api/models {name, endpoint}` (201; 409 on
 * a duplicate name, 422 on an invalid name/endpoint — surfaced as a toast),
 * `DELETE /api/models/{name}` (built-ins are undeletable server-side, so the
 * remove button is only rendered for non-builtin rows). Lives outside
 * `components/preview/` now that it fetches (§8 grep guard). The PREVIEW tag
 * still reflects `flags.localModels: "preview"` — registration is real, but
 * routing a chat call to a registered local endpoint is still stubbed
 * server-side.
 */
export default function ModelsPanel() {
  const { data: models, isLoading, mutate } = useModels();
  const { show } = useToast();
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [busy, setBusy] = useState(false);

  async function addModel(event: React.FormEvent) {
    event.preventDefault();
    const trimmedName = name.trim();
    const trimmedEndpoint = endpoint.trim();
    if (!trimmedName || !trimmedEndpoint || busy) return;
    setBusy(true);
    try {
      await mutateJSON("/api/models", { name: trimmedName, endpoint: trimmedEndpoint });
      setName("");
      setEndpoint("");
      show(`model :: ${trimmedName} registered`);
      mutate();
    } catch (error) {
      show(`model :: ${error instanceof ApiError ? error.message : "add failed — backend offline?"}`);
    } finally {
      setBusy(false);
    }
  }

  async function removeModel(target: string) {
    try {
      await mutateJSON(`/api/models/${encodeURIComponent(target)}`, undefined, "DELETE");
      show(`model :: ${target} removed`);
      mutate();
    } catch (error) {
      show(`model :: ${error instanceof ApiError ? error.message : "remove failed — backend offline?"}`);
    }
  }

  return (
    <Panel label="MODELS" preview={FLAGS.localModels === "preview"}>
      <ul className="space-y-1.5">
        {(models ?? []).map((model) => (
          <li key={model.name} className="group flex items-center gap-2.5 py-1">
            <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-ink">{model.name}</span>
            <span className="border border-line px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-faint">
              {model.builtin ? "API" : "LOCAL"}
            </span>
            {model.default && (
              <span className="border border-[var(--ac)] px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-[var(--ac)]">
                DEFAULT
              </span>
            )}
            {model.endpoint && (
              <span className="truncate font-mono text-[10.5px] text-ink-faint">{model.endpoint}</span>
            )}
            {!model.builtin && (
              <button
                type="button"
                aria-label={`Remove ${model.name}`}
                onClick={() => removeModel(model.name)}
                className="hidden shrink-0 font-mono text-xs text-ink-faint hover:text-danger group-hover:inline"
              >
                ×
              </button>
            )}
          </li>
        ))}
        {isLoading && !models && <li className="font-mono text-[11px] text-ink-faint">loading…</li>}
        {!isLoading && models && models.length === 0 && (
          <li className="font-mono text-[11px] text-ink-faint">no models registered</li>
        )}
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
          disabled={!name.trim() || !endpoint.trim() || busy}
          className="shrink-0 border border-line px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
        >
          {busy ? "ADDING…" : "+ ADD MODEL"}
        </button>
      </form>
    </Panel>
  );
}
