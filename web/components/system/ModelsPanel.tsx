"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import AddModelDialog from "@/components/system/AddModelDialog";
import { ApiError, mutateJSON, setDefaultModel, useModels, type ModelInfo } from "@/lib/api";

/**
 * MODELS (§7, §12) — the registry, wired to `GET /api/models`,
 * `DELETE /api/models/{name}` and `POST /api/models/default`. Adding a model
 * goes through {@link AddModelDialog}, which tests the connection before it
 * saves; this panel is the list plus the two per-row actions.
 *
 * The LOCAL/HOSTED badge reads the backend's `local` flag rather than
 * inferring from `builtin`: what matters to a user is whether their notes stay
 * on this machine, and a hosted open-weight endpoint is just as much a network
 * hop as Claude is.
 */
export default function ModelsPanel() {
  const { data: models, isLoading, mutate } = useModels();
  const { show } = useToast();
  const [adding, setAdding] = useState(false);
  const [busyName, setBusyName] = useState<string | null>(null);

  async function removeModel(target: string) {
    setBusyName(target);
    try {
      await mutateJSON(`/api/models/${encodeURIComponent(target)}`, undefined, "DELETE");
      show(`model :: ${target} removed`);
      mutate();
    } catch (error) {
      show(
        `model :: ${error instanceof ApiError ? error.message : "remove failed — backend offline?"}`,
      );
    } finally {
      setBusyName(null);
    }
  }

  async function makeDefault(target: string) {
    setBusyName(target);
    try {
      await setDefaultModel(target);
      show(`model :: ${target} is now the default`);
      mutate();
    } catch (error) {
      show(
        `model :: ${error instanceof ApiError ? error.message : "could not set the default"}`,
      );
    } finally {
      setBusyName(null);
    }
  }

  return (
    <Panel
      label="MODELS"
      headerRight={
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="border border-line px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
        >
          + ADD MODEL
        </button>
      }
    >
      <ul className="space-y-1.5">
        {(models ?? []).map((model) => (
          <ModelRow
            key={model.name}
            model={model}
            busy={busyName === model.name}
            onRemove={() => removeModel(model.name)}
            onMakeDefault={() => makeDefault(model.name)}
          />
        ))}
        {isLoading && !models && <li className="font-mono text-[11px] text-ink-faint">loading…</li>}
        {!isLoading && models && models.length === 0 && (
          <li className="font-mono text-[11px] text-ink-faint">no models registered</li>
        )}
      </ul>

      <p className="mt-3 border-t border-line pt-2 text-[10.5px] leading-relaxed text-ink-faint">
        <span className="text-ok">LOCAL</span> models run on this computer and your notes never
        leave it. <span className="text-ink-muted">HOSTED</span> models send note excerpts to that
        provider.
      </p>

      {adding && <AddModelDialog onClose={() => setAdding(false)} onAdded={() => mutate()} />}
    </Panel>
  );
}

function ModelRow({
  model,
  busy,
  onRemove,
  onMakeDefault,
}: {
  model: ModelInfo;
  busy: boolean;
  onRemove: () => void;
  onMakeDefault: () => void;
}) {
  return (
    <li className="group flex flex-wrap items-center gap-2.5 py-1">
      <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-ink">{model.name}</span>

      <span
        className={`shrink-0 border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] ${
          model.local ? "border-ok text-ok" : "border-line text-ink-faint"
        }`}
        title={
          model.local
            ? "Runs on this computer — your notes never leave it"
            : "Note excerpts are sent to this provider"
        }
      >
        {model.local ? "LOCAL" : "HOSTED"}
      </span>

      {model.has_key && (
        <span
          className="shrink-0 border border-line px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-faint"
          title="An API key is saved in your OS keyring for this model"
        >
          KEY
        </span>
      )}

      {model.default ? (
        <span className="shrink-0 border border-[var(--ac)] px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-[var(--ac)]">
          DEFAULT
        </span>
      ) : (
        <button
          type="button"
          onClick={onMakeDefault}
          disabled={busy}
          className="hidden shrink-0 border border-line px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-lineHi hover:text-ink disabled:opacity-40 group-hover:inline-block"
        >
          MAKE DEFAULT
        </button>
      )}

      {model.endpoint && (
        <span className="max-w-[180px] truncate font-mono text-[10.5px] text-ink-faint">
          {model.endpoint}
        </span>
      )}

      {!model.builtin && (
        <button
          type="button"
          aria-label={`Remove ${model.name}`}
          onClick={onRemove}
          disabled={busy}
          className="hidden shrink-0 font-mono text-xs text-ink-faint hover:text-danger disabled:opacity-40 group-hover:inline"
        >
          ×
        </button>
      )}
    </li>
  );
}
