"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import {
  addModel,
  testModel,
  type ModelProvider,
  type TestModelResult,
} from "@/lib/api";

/**
 * Guided model setup (§7). Four provider choices, fields that adapt to the
 * one picked, and a Test button that must go green before Save unlocks.
 *
 * The test-before-save gate is the point: without it a non-developer finds out
 * their endpoint is wrong, their key is rejected, or their model cannot call
 * tools during their first real question, with an error written for a
 * developer. Here they find out immediately, and the test also lists what the
 * endpoint actually serves so the model is a dropdown rather than a guess.
 */

interface ProviderOption {
  id: ModelProvider;
  title: string;
  blurb: string;
  privacy: string;
  local: boolean;
  needsEndpoint: boolean;
  needsKey: boolean;
  defaultEndpoint?: string;
}

const PROVIDERS: ProviderOption[] = [
  {
    id: "openai-compat",
    title: "Ollama on this PC",
    blurb: "Free, runs offline. Needs Ollama installed.",
    privacy: "Your notes never leave this computer.",
    local: true,
    needsEndpoint: true,
    needsKey: false,
    defaultEndpoint: "http://localhost:11434/v1",
  },
  {
    id: "openai-compat",
    title: "Hosted API",
    blurb: "Groq, Together, Fireworks, OpenRouter, and similar.",
    privacy: "Note excerpts are sent to that company's servers.",
    local: false,
    needsEndpoint: true,
    needsKey: true,
  },
  {
    id: "anthropic-api",
    title: "Anthropic API key",
    blurb: "Claude, billed to your API account. No Claude Code needed.",
    privacy: "Note excerpts are sent to Anthropic.",
    local: false,
    needsEndpoint: false,
    needsKey: true,
  },
  {
    id: "anthropic",
    title: "Claude Code",
    blurb: "Claude on your existing subscription. Needs Claude Code installed.",
    privacy: "Note excerpts are sent to Anthropic.",
    local: false,
    needsEndpoint: false,
    needsKey: false,
  },
];

/** Base URLs for the hosted providers people actually use, so nobody hunts for them. */
const HOSTED_PRESETS: { label: string; endpoint: string }[] = [
  { label: "Groq", endpoint: "https://api.groq.com/openai/v1" },
  { label: "Together", endpoint: "https://api.together.xyz/v1" },
  { label: "Fireworks", endpoint: "https://api.fireworks.ai/inference/v1" },
  { label: "OpenRouter", endpoint: "https://openrouter.ai/api/v1" },
  { label: "DeepInfra", endpoint: "https://api.deepinfra.com/v1/openai" },
];

const INPUT =
  "w-full border border-line bg-sunken px-2.5 py-1.5 text-[12.5px] text-ink placeholder:text-ink-faint focus:border-lineHi focus:outline-none";
const BUTTON =
  "shrink-0 border border-line px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40";

export default function AddModelDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: () => void;
}) {
  const { show } = useToast();
  const [choice, setChoice] = useState(0);
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState(PROVIDERS[0].defaultEndpoint ?? "");
  const [apiKey, setApiKey] = useState("");
  const [modelId, setModelId] = useState("");
  const [result, setResult] = useState<TestModelResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  const provider = PROVIDERS[choice];
  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    restoreRef.current = document.activeElement as HTMLElement | null;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const restore = restoreRef.current;
    return () => {
      window.removeEventListener("keydown", onKey);
      restore?.focus?.();
    };
  }, [onClose]);

  // Any edit invalidates a previous green light — otherwise someone could test
  // one configuration and save a different one.
  function invalidate() {
    setResult(null);
  }

  function pickProvider(index: number) {
    setChoice(index);
    setEndpoint(PROVIDERS[index].defaultEndpoint ?? "");
    setApiKey("");
    setModelId("");
    invalidate();
  }

  const canTest = useMemo(() => {
    if (provider.needsEndpoint && !endpoint.trim()) return false;
    if (provider.needsKey && !apiKey.trim()) return false;
    return true;
  }, [provider, endpoint, apiKey]);

  const tested = result?.ok === true && (provider.id === "anthropic" || Boolean(modelId.trim()));
  const canSave = Boolean(name.trim()) && (tested || provider.id === "anthropic");

  async function runTest() {
    if (!canTest || testing) return;
    setTesting(true);
    try {
      const outcome = await testModel({
        provider: provider.id,
        endpoint: provider.needsEndpoint ? endpoint.trim() : undefined,
        api_key: provider.needsKey ? apiKey.trim() : undefined,
        model_id: modelId.trim() || undefined,
        name: name.trim() || undefined,
      });
      setResult(outcome);
      // The endpoint told us what it serves — offer the first one so a
      // non-developer never has to know a model id by heart.
      if (!modelId.trim() && outcome.available_models.length > 0) {
        setModelId(outcome.available_models[0]);
      }
    } catch (error) {
      setResult({
        ok: false,
        detail: error instanceof Error ? error.message : "the test could not run",
        tool_calling: false,
        latency_ms: 0,
        available_models: [],
      });
    } finally {
      setTesting(false);
    }
  }

  async function save() {
    if (!canSave || saving) return;
    setSaving(true);
    try {
      await addModel({
        name: name.trim(),
        provider: provider.id,
        endpoint: provider.needsEndpoint ? endpoint.trim() : undefined,
        api_key: provider.needsKey ? apiKey.trim() : undefined,
        model_id: modelId.trim() || undefined,
      });
      show(`model :: ${name.trim()} added`);
      onAdded();
      onClose();
    } catch (error) {
      show(`model :: ${error instanceof Error ? error.message : "could not add that model"}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 overflow-y-auto bg-[rgba(3,2,8,0.72)]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Add a model"
        className="animate-palette mx-auto my-[8vh] w-[620px] max-w-[calc(100vw-2rem)] border border-lineHi bg-panel p-5"
      >
        <p className="eyebrow mb-3">▍ADD.MODEL</p>

        {/* 1 — provider */}
        <fieldset className="mb-4">
          <legend className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
            1 · where should it run?
          </legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {PROVIDERS.map((option, index) => {
              const active = index === choice;
              return (
                <button
                  key={option.title}
                  type="button"
                  aria-pressed={active}
                  onClick={() => pickProvider(index)}
                  className={`border p-2.5 text-left transition-colors ${
                    active
                      ? "border-[var(--ac)] bg-[var(--ac-bg)]"
                      : "border-line hover:border-lineHi"
                  }`}
                >
                  <span className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink">
                      {option.title}
                    </span>
                    <span
                      className={`shrink-0 border px-1 py-px font-mono text-[8px] uppercase tracking-[0.14em] ${
                        option.local ? "border-ok text-ok" : "border-line text-ink-faint"
                      }`}
                    >
                      {option.local ? "LOCAL" : "HOSTED"}
                    </span>
                  </span>
                  <span className="mt-1 block text-[11px] leading-relaxed text-ink-muted">
                    {option.blurb}
                  </span>
                  <span className="mt-1 block text-[10.5px] leading-relaxed text-ink-faint">
                    {option.privacy}
                  </span>
                </button>
              );
            })}
          </div>
        </fieldset>

        {/* 2 — connection */}
        <fieldset className="mb-4 flex flex-col gap-2">
          <legend className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
            2 · connection
          </legend>

          {provider.needsEndpoint && (
            <>
              {!provider.local && (
                <div className="flex flex-wrap gap-1.5">
                  {HOSTED_PRESETS.map((preset) => (
                    <button
                      key={preset.label}
                      type="button"
                      onClick={() => {
                        setEndpoint(preset.endpoint);
                        invalidate();
                      }}
                      className="border border-line px-1.5 py-0.5 font-mono text-[10px] text-ink-faint transition-colors hover:border-lineHi hover:text-ink"
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>
              )}
              <label className="flex flex-col gap-1">
                <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint">
                  server address
                </span>
                <input
                  value={endpoint}
                  onChange={(event) => {
                    setEndpoint(event.target.value);
                    invalidate();
                  }}
                  placeholder="http://localhost:11434/v1"
                  className={INPUT}
                />
              </label>
            </>
          )}

          {provider.needsKey && (
            <label className="flex flex-col gap-1">
              <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint">
                api key
              </span>
              <input
                type="password"
                value={apiKey}
                onChange={(event) => {
                  setApiKey(event.target.value);
                  invalidate();
                }}
                placeholder="pasted from your provider's dashboard"
                className={INPUT}
              />
              <span className="text-[10.5px] text-ink-faint">
                Stored in your operating system&apos;s keyring, never in a file.
              </span>
            </label>
          )}

          {provider.id === "anthropic" && (
            <p className="text-[11.5px] leading-relaxed text-ink-muted">
              Uses the Claude Code CLI you already signed in to. Nothing to configure here — enter
              the Claude model name below, for example <code>claude-sonnet-5</code>.
            </p>
          )}

          {provider.id !== "anthropic" && (
            <div className="flex items-center gap-2">
              <button type="button" onClick={runTest} disabled={!canTest || testing} className={BUTTON}>
                {testing ? "TESTING…" : "TEST CONNECTION"}
              </button>
              {result && (
                <p
                  className={`min-w-0 flex-1 text-[11.5px] leading-relaxed ${
                    result.ok ? "text-ok" : "text-danger"
                  }`}
                  role="status"
                >
                  {result.ok ? "✓" : "✗"} {result.detail}
                  {result.ok && result.latency_ms > 0 && (
                    <span className="text-ink-faint"> · {result.latency_ms}ms</span>
                  )}
                </p>
              )}
            </div>
          )}
        </fieldset>

        {/* 3 — model */}
        <fieldset className="mb-4 flex flex-col gap-2">
          <legend className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
            3 · which model?
          </legend>
          {result && result.available_models.length > 0 ? (
            <select
              value={modelId}
              onChange={(event) => {
                setModelId(event.target.value);
                invalidate();
              }}
              aria-label="Model"
              className={INPUT}
            >
              <option value="">choose a model…</option>
              {result.available_models.map((available) => (
                <option key={available} value={available}>
                  {available}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={modelId}
              onChange={(event) => {
                setModelId(event.target.value);
                invalidate();
              }}
              placeholder={
                provider.id === "anthropic" ? "claude-sonnet-5" : "test the connection to list models"
              }
              aria-label="Model"
              className={INPUT}
            />
          )}
          {result?.ok && !result.tool_calling && modelId && (
            <p className="text-[11px] text-ink-faint">
              Run the test again after choosing a model — Argus checks that it can use your notes.
            </p>
          )}
        </fieldset>

        {/* 4 — name */}
        <fieldset className="mb-4 flex flex-col gap-1">
          <legend className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
            4 · name it
          </legend>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="what you'll see in the model menu, e.g. groq-llama"
            aria-label="Display name"
            className={INPUT}
          />
        </fieldset>

        <div className="flex items-center gap-2 border-t border-line pt-3">
          <button type="button" onClick={onClose} className={BUTTON}>
            CANCEL
          </button>
          <button
            type="button"
            onClick={save}
            disabled={!canSave || saving}
            className={`${BUTTON} ml-auto`}
          >
            {saving ? "SAVING…" : "SAVE MODEL"}
          </button>
        </div>
        {!canSave && (
          <p className="mt-2 text-right text-[10.5px] text-ink-faint">
            {provider.id === "anthropic"
              ? "Enter a model name and a display name."
              : "Pass the connection test, then name it."}
          </p>
        )}
      </div>
    </div>
  );
}
