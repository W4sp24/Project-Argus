"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import {
  installModel,
  useModelCatalog,
  useModels,
  type CatalogEntry,
  type FitVerdict,
  type HardwareInfo,
} from "@/lib/api";

/**
 * LOCAL.MODELS (§7) — the "run it on your own PC" path, made choosable.
 *
 * Every model listed can call tools, because Argus has no no-tools fallback;
 * and each is scored against the detected machine, because local inference
 * needs *more* horsepower than a hosted call, not less. Between them that
 * turns "which model should I download?" — a question a non-developer cannot
 * answer — into a badge and a button.
 */

const VERDICT_STYLE: Record<FitVerdict, { label: string; className: string }> = {
  fits: { label: "FITS", className: "border-ok text-ok" },
  slow: { label: "SLOW", className: "border-[var(--ac)] text-[var(--ac)]" },
  insufficient: { label: "TOO BIG", className: "border-line text-ink-faint" },
  unknown: { label: "UNKNOWN", className: "border-line text-ink-faint" },
};

export default function LocalModelBrowser() {
  const { data, isLoading, mutate } = useModelCatalog();
  const { mutate: mutateModels } = useModels();
  const { show } = useToast();
  const [progress, setProgress] = useState<Record<string, string>>({});
  const [installing, setInstalling] = useState<string | null>(null);

  async function download(entry: CatalogEntry) {
    if (installing) return;
    setInstalling(entry.name);
    setProgress((current) => ({ ...current, [entry.name]: "starting…" }));
    try {
      await installModel(entry.name, (event) => {
        if (event.type === "error") {
          show(`model :: ${event.detail ?? "download failed"}`);
          setProgress((current) => ({ ...current, [entry.name]: `failed — ${event.detail ?? ""}` }));
          return;
        }
        if (event.type === "done") {
          setProgress((current) => ({ ...current, [entry.name]: "installed" }));
          show(`model :: ${entry.name} installed and ready`);
          return;
        }
        setProgress((current) => ({ ...current, [entry.name]: describe(event) }));
      });
    } catch (error) {
      show(`model :: ${error instanceof Error ? error.message : "download failed"}`);
    } finally {
      setInstalling(null);
      mutate();
      mutateModels();
    }
  }

  const entries = data?.models ?? [];

  return (
    <Panel label="LOCAL.MODELS">
      <HardwareLine hardware={data?.hardware} loading={isLoading && !data} />

      <ul className="mt-3 space-y-2">
        {entries.map((entry) => {
          const style = VERDICT_STYLE[entry.verdict];
          const status = progress[entry.name];
          const isRecommended = data?.recommended === entry.name;
          return (
            <li key={entry.name} className="border border-line p-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-body text-ink">
                  {entry.label}
                  <span className="ml-1.5 font-mono text-meta text-ink-faint">
                    {entry.name}
                  </span>
                </span>

                <span
                  className={`shrink-0 border px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.1em] ${style.className}`}
                  title={entry.reason}
                >
                  {style.label}
                </span>

                {isRecommended && (
                  <span className="shrink-0 border border-[var(--ac)] px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.1em] text-[var(--ac)]">
                    BEST FIT
                  </span>
                )}

                <span className="shrink-0 font-mono text-meta text-ink-faint">
                  {entry.size_gb}GB · {entry.parameters}
                </span>

                {entry.installed ? (
                  <span className="shrink-0 font-mono text-meta uppercase tracking-[0.1em] text-ok">
                    ✓ added
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => download(entry)}
                    disabled={Boolean(installing) || entry.verdict === "insufficient"}
                    title={
                      entry.verdict === "insufficient"
                        ? entry.reason
                        : `Download ${entry.label} through Ollama`
                    }
                    className="shrink-0 border border-line px-2 py-0.5 font-mono text-meta uppercase tracking-[0.1em] text-ink transition-colors hover:border-lineHi disabled:opacity-40"
                  >
                    {installing === entry.name ? "DOWNLOADING…" : "DOWNLOAD"}
                  </button>
                )}
              </div>

              <p className="mt-1.5 text-label leading-relaxed text-ink-muted">{entry.summary}</p>
              <p className="mt-0.5 text-meta leading-relaxed text-ink-faint">{entry.reason}</p>
              {status && (
                <p className="mt-1 font-mono text-meta text-[var(--ac)]" role="status">
                  {status}
                </p>
              )}
            </li>
          );
        })}
        {isLoading && !data && (
          <li className="font-mono text-label text-ink-faint">checking your hardware…</li>
        )}
      </ul>

      {data && (
        <p className="mt-3 border-t border-line pt-2 text-meta leading-relaxed text-ink-faint">
          Downloads go through Ollama at {data.hardware.ollama_url}, which stores them in{" "}
          <code>{data.hardware.ollama_models_dir}</code>. To keep them elsewhere, set Ollama&apos;s{" "}
          <code>OLLAMA_MODELS</code> folder before downloading.
        </p>
      )}
    </Panel>
  );
}

function HardwareLine({ hardware, loading }: { hardware?: HardwareInfo; loading: boolean }) {
  if (loading) return <p className="font-mono text-label text-ink-faint">detecting…</p>;
  if (!hardware) {
    return (
      <p className="font-mono text-label text-ink-faint">
        could not read this machine&apos;s specs — backend offline?
      </p>
    );
  }
  return (
    <p className="font-mono text-label text-ink-muted">
      this pc ::{" "}
      <span className="text-ink">{hardware.ram_gb ? `${hardware.ram_gb}GB RAM` : "RAM unknown"}</span>
      {" · "}
      <span className="text-ink">
        {hardware.vram_gb
          ? `${hardware.gpu_name ?? "GPU"}, ${hardware.vram_gb}GB`
          : "no NVIDIA GPU detected"}
      </span>
    </p>
  );
}

/** Turn Ollama's raw pull events into one readable line. */
function describe(event: { status?: string; completed?: number; total?: number }): string {
  const status = event.status ?? "working";
  if (event.total && event.completed !== undefined && event.total > 0) {
    const pct = Math.min(100, Math.round((event.completed / event.total) * 100));
    const gb = (event.total / 1024 ** 3).toFixed(1);
    return `${status} — ${pct}% of ${gb}GB`;
  }
  return status;
}
