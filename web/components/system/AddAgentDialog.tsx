"use client";

import { useEffect, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import Stepper from "@/components/ui/Stepper";
import { addCustomAgent, scanAgentFolder, type AgentScanResult } from "@/lib/api";
import { compactTokens } from "@/lib/agentPalette";

/**
 * Track a new agent by pointing Argus at its log folder.
 *
 * Adding an agent used to mean writing a Python class and shipping a release,
 * which meant the multi-agent panel only ever knew the agents we had thought
 * of. Now the folder is the input: Argus samples it, tries every log format it
 * knows, and reports what it actually found — turn count, models, date range —
 * before anything is saved.
 *
 * That report is the gate, deliberately the same shape as the model wizard's
 * connection test. Without it you could register a folder that will never
 * produce a single row and only discover it days later when the agent is still
 * sitting at zero.
 */

/** Log folders worth guessing at, so nobody types a path they could pick. */
const KNOWN_ROOTS: { label: string; path: string }[] = [
  { label: "Codex", path: "~/.codex/sessions" },
  { label: "Gemini", path: "~/.gemini/tmp" },
  { label: "Cursor", path: "~/.cursor" },
];

type StepKey = "folder" | "check" | "name";

const STEPS: StepKey[] = ["folder", "check", "name"];
const STEP_LABEL: Record<StepKey, string> = {
  folder: "FOLDER",
  check: "CHECK",
  name: "NAME",
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3">
      <dt className="w-20 shrink-0 text-ink-faint">{label}</dt>
      <dd className="min-w-0 flex-1 break-words text-ink-muted">{value}</dd>
    </div>
  );
}

function ScanOutcome({ result }: { result: AgentScanResult }) {
  return (
    <div
      role="status"
      className={`border px-3 py-2 ${
        result.ok
          ? "border-ok bg-[rgba(52,211,153,0.06)]"
          : "border-danger bg-[rgba(251,113,133,0.06)]"
      }`}
    >
      <p className={`text-label leading-relaxed ${result.ok ? "text-ok" : "text-danger"}`}>
        <span aria-hidden className="mr-1 font-mono">
          {result.ok ? "✓" : "✗"}
        </span>
        {result.detail}
      </p>

      {result.ok && (
        <dl className="mt-2 flex flex-col gap-1 font-mono text-meta">
          <Stat label="format" value={result.format_label ?? "—"} />
          <Stat label="pattern" value={result.glob} />
          <Stat label="tokens" value={result.total_tokens.toLocaleString()} />
          <Stat
            label="models"
            value={result.models.length > 0 ? result.models.join(", ") : "none named"}
          />
          {result.first_ts && result.last_ts && (
            <Stat
              label="range"
              value={`${result.first_ts.slice(0, 10)} → ${result.last_ts.slice(0, 10)}`}
            />
          )}
        </dl>
      )}

      {result.ok && result.sampled && (
        <p className="mt-2 text-meta leading-relaxed text-ink-faint">
          Those totals come from a sample of the {result.files} matching files. Everything gets
          counted once the agent is saved.
        </p>
      )}
    </div>
  );
}

export default function AddAgentDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: () => void;
}) {
  const { show } = useToast();
  const [step, setStep] = useState(0);
  const [furthest, setFurthest] = useState(0);
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [result, setResult] = useState<AgentScanResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const stepKey = STEPS[Math.min(step, STEPS.length - 1)];
  const bodyRef = useRef<HTMLDivElement>(null);
  const mounted = useRef(false);

  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    const body = bodyRef.current;
    const target =
      body?.querySelector<HTMLElement>("input, select, textarea") ??
      body?.querySelector<HTMLElement>("button:not([disabled])");
    target?.focus();
  }, [step]);

  /** Editing the folder un-proves whatever the last scan said about it. */
  function invalidate() {
    setResult(null);
    setSaveError(null);
    setFurthest((far) => Math.min(far, 0));
  }

  async function runScan() {
    if (!path.trim() || scanning) return;
    setScanning(true);
    setSaveError(null);
    try {
      const outcome = await scanAgentFolder({ path: path.trim() });
      setResult(outcome);
      if (outcome.ok) {
        setStep(1);
        setFurthest((far) => Math.max(far, 1));
      }
    } catch (error) {
      setResult({
        ok: false,
        detail: error instanceof Error ? error.message : "the scan could not run",
        format: null,
        format_label: null,
        glob: "",
        files: 0,
        turns: 0,
        total_tokens: 0,
        models: [],
        first_ts: null,
        last_ts: null,
        sampled: false,
        sample_paths: [],
      });
    } finally {
      setScanning(false);
    }
  }

  async function save() {
    if (!name.trim() || !result?.ok || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      await addCustomAgent({
        name: name.trim(),
        path: path.trim(),
        glob: result.glob || undefined,
        format: result.format ?? undefined,
      });
      show(`agent :: ${name.trim()} tracked`);
      onAdded();
      onClose();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "could not add that agent");
    } finally {
      setSaving(false);
    }
  }

  const canAdvance = stepKey === "folder" ? result?.ok === true : stepKey === "check";
  const canSave = Boolean(name.trim()) && result?.ok === true;

  return (
    <Dialog
      label="Add an agent"
      onClose={onClose}
      align="center"
      className="w-[38rem] max-w-[calc(100vw-2rem)]"
    >
      <header className="border-b border-line px-5 pb-4 pt-5">
        <p className="eyebrow">▍ADD.AGENT</p>
        <Stepper
          steps={STEPS.map((key) => STEP_LABEL[key])}
          current={step}
          furthest={furthest}
          onJump={setStep}
          className="mt-3"
        />
      </header>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (stepKey === "name") {
            void save();
          } else if (stepKey === "folder" && result?.ok !== true) {
            // Enter on an unproven folder scans it; once proven, it advances —
            // otherwise coming BACK to this step would re-scan instead of moving on.
            void runScan();
          } else {
            const next = step + 1;
            setStep(next);
            setFurthest((far) => Math.max(far, next));
          }
        }}
      >
        <div ref={bodyRef} className="min-h-[17rem] px-5 py-5">
          {stepKey === "folder" && (
            <div className="flex flex-col gap-3">
              <p className="text-label leading-relaxed text-ink-muted">
                Point Argus at the folder your agent writes session logs to. It reads a sample and
                works out the format — you do not need to know which one it uses.
              </p>

              <div className="flex flex-wrap gap-1.5">
                {KNOWN_ROOTS.map((preset) => (
                  <Button
                    key={preset.label}
                    variant={path === preset.path ? "primary" : "quiet"}
                    className="normal-case tracking-normal"
                    onClick={() => {
                      setPath(preset.path);
                      invalidate();
                    }}
                  >
                    {preset.label}
                  </Button>
                ))}
              </div>

              <Field label="log folder" hint="~ works. Nothing is written to this folder.">
                {(props) => (
                  <input
                    {...props}
                    value={path}
                    onChange={(event) => {
                      setPath(event.target.value);
                      invalidate();
                    }}
                    placeholder="~/.codex/sessions"
                    className={`${FIELD_CONTROL} font-mono text-meta`}
                  />
                )}
              </Field>

              <div>
                <Button size="md" variant="primary" onClick={runScan} disabled={!path.trim() || scanning}>
                  {scanning ? "SCANNING…" : "SCAN FOLDER"}
                </Button>
              </div>

              {result && !result.ok && <ScanOutcome result={result} />}
            </div>
          )}

          {stepKey === "check" && result && (
            <div className="flex flex-col gap-3">
              <p className="text-label text-ink-muted">This is what Argus found. Look right?</p>
              <ScanOutcome result={result} />
              {result.sample_paths.length > 0 && (
                <div>
                  <p className="mb-1 font-mono text-micro uppercase tracking-[0.14em] text-ink-faint">
                    matched files
                  </p>
                  <ul className="flex flex-col gap-0.5">
                    {result.sample_paths.map((sample) => (
                      <li key={sample} className="break-all font-mono text-micro text-ink-faint">
                        {sample}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {stepKey === "name" && (
            <div className="flex flex-col gap-4">
              <Field label="display name" hint="What you'll see in the agent selector.">
                {(props) => (
                  <input
                    {...props}
                    value={name}
                    onChange={(event) => {
                      setName(event.target.value);
                      setSaveError(null);
                    }}
                    placeholder="e.g. Gemini CLI"
                    className={FIELD_CONTROL}
                  />
                )}
              </Field>

              <div className="border border-line bg-sunken px-3 py-2.5">
                <p className="mb-2 font-mono text-micro uppercase tracking-[0.14em] text-ink-faint">
                  about to track
                </p>
                <dl className="flex flex-col gap-1 font-mono text-meta">
                  <Stat label="folder" value={path.trim()} />
                  <Stat label="format" value={result?.format_label ?? "—"} />
                  <Stat
                    label="found"
                    value={`${compactTokens(result?.total_tokens ?? 0)} tokens · ${result?.files ?? 0} files`}
                  />
                  <Stat label="cost" value="unpriced — Argus has no rates for these models" />
                </dl>
              </div>

              {saveError && (
                <p role="alert" className="border border-danger px-3 py-2 text-label text-danger">
                  {saveError}
                </p>
              )}
            </div>
          )}
        </div>

        <footer className="flex items-center gap-2 border-t border-line px-5 pb-5 pt-4">
          <Button size="md" onClick={onClose}>
            CANCEL
          </Button>
          {step > 0 && (
            <Button size="md" onClick={() => setStep(step - 1)}>
              ← BACK
            </Button>
          )}
          <div className="ml-auto flex items-center gap-3">
            {stepKey === "folder" && !canAdvance && (
              <p className="text-right text-meta text-ink-faint">
                Scan a folder Argus can read to continue.
              </p>
            )}
            {stepKey === "name" && !canSave && (
              <p className="text-right text-meta text-ink-faint">Give it a name to save.</p>
            )}
            {stepKey === "name" ? (
              <Button type="submit" size="md" variant="primary" disabled={!canSave || saving}>
                {saving ? "SAVING…" : "TRACK AGENT"}
              </Button>
            ) : (
              <Button type="submit" size="md" variant="primary" disabled={!canAdvance}>
                NEXT →
              </Button>
            )}
          </div>
        </footer>
      </form>
    </Dialog>
  );
}
