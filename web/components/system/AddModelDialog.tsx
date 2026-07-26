"use client";

import { useMemo, useState } from "react";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
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
    // 127.0.0.1 rather than "localhost": that name resolves to ::1 first, and
    // Ollama binds the IPv4 loopback, so every request would pay a failed IPv6
    // attempt before falling through.
    defaultEndpoint: "http://127.0.0.1:11434/v1",
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

const LEGEND = "mb-2 font-mono text-meta uppercase tracking-[0.14em] text-ink-faint";

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
  const [saveError, setSaveError] = useState<string | null>(null);

  const provider = PROVIDERS[choice];

  // Any edit invalidates a previous green light — otherwise someone could test
  // one configuration and save a different one.
  function invalidate() {
    setResult(null);
    setSaveError(null);
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
    setSaveError(null);
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
    setSaveError(null);
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
      // Inline rather than a toast: the dialog stays open on failure, and a
      // 3.2s line in the opposite corner was the only thing saying why.
      setSaveError(error instanceof Error ? error.message : "could not add that model");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      label="Add a model"
      onClose={onClose}
      align="center"
      className="w-[38.75rem] max-w-[calc(100vw-2rem)] p-5"
    >
      <p className="eyebrow mb-3">▍ADD.MODEL</p>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          // Enter does whatever the next step is: test while the connection is
          // unproven, save once it is.
          if (canSave) void save();
          else if (canTest && !testing && provider.id !== "anthropic") void runTest();
        }}
      >
        {/* 1 — provider */}
        <fieldset className="mb-4">
          <legend className={LEGEND}>1 · where should it run?</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {PROVIDERS.map((option, index) => {
              const active = index === choice;
              return (
                <button
                  key={option.title}
                  type="button"
                  aria-pressed={active}
                  onClick={() => pickProvider(index)}
                  className={`border p-3 text-left transition-colors ${
                    active
                      ? "border-[var(--ac)] bg-[var(--ac-bg)]"
                      : "border-line hover:border-lineHi"
                  }`}
                >
                  <span className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-body text-ink">
                      {option.title}
                    </span>
                    <span
                      className={`shrink-0 border px-1.5 py-px font-mono text-micro uppercase tracking-[0.14em] ${
                        option.local ? "border-ok text-ok" : "border-line text-ink-faint"
                      }`}
                    >
                      {option.local ? "LOCAL" : "HOSTED"}
                    </span>
                  </span>
                  <span className="mt-1 block text-label text-ink-muted">{option.blurb}</span>
                  <span className="mt-1 block text-meta text-ink-faint">{option.privacy}</span>
                </button>
              );
            })}
          </div>
        </fieldset>

        {/* 2 — connection */}
        <fieldset className="mb-4 flex flex-col gap-3">
          <legend className={LEGEND}>2 · connection</legend>

          {provider.needsEndpoint && (
            <>
              {!provider.local && (
                <div className="flex flex-wrap gap-1.5">
                  {HOSTED_PRESETS.map((preset) => (
                    <Button
                      key={preset.label}
                      variant="quiet"
                      className="normal-case tracking-normal"
                      onClick={() => {
                        setEndpoint(preset.endpoint);
                        invalidate();
                      }}
                    >
                      {preset.label}
                    </Button>
                  ))}
                </div>
              )}
              <Field label="server address">
                {(props) => (
                  <input
                    {...props}
                    value={endpoint}
                    onChange={(event) => {
                      setEndpoint(event.target.value);
                      invalidate();
                    }}
                    placeholder="http://127.0.0.1:11434/v1"
                    className={FIELD_CONTROL}
                  />
                )}
              </Field>
            </>
          )}

          {provider.needsKey && (
            <Field
              label="api key"
              hint="Stored in your operating system's keyring, never in a file."
            >
              {(props) => (
                <input
                  {...props}
                  type="password"
                  value={apiKey}
                  onChange={(event) => {
                    setApiKey(event.target.value);
                    invalidate();
                  }}
                  placeholder="pasted from your provider's dashboard"
                  className={FIELD_CONTROL}
                />
              )}
            </Field>
          )}

          {provider.id === "anthropic" && (
            <p className="text-label leading-relaxed text-ink-muted">
              Uses the Claude Code CLI you already signed in to. Nothing to configure here — enter
              the Claude model name below, for example <code>claude-sonnet-5</code>.
            </p>
          )}

          {provider.id !== "anthropic" && (
            <div className="flex flex-wrap items-center gap-2">
              <Button size="md" onClick={runTest} disabled={!canTest || testing}>
                {testing ? "TESTING…" : "TEST CONNECTION"}
              </Button>
              {result && (
                <p
                  className={`min-w-0 flex-1 text-label leading-relaxed ${
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
          <legend className={LEGEND}>3 · which model?</legend>
          {/* The numbered legends are the visible step headers, so these labels
              are hidden — they exist so each control still has a real
              accessible name rather than a placeholder standing in for one. */}
          <Field label="Model" visuallyHiddenLabel>
            {(props) =>
              result && result.available_models.length > 0 ? (
                <select
                  {...props}
                  value={modelId}
                  onChange={(event) => {
                    setModelId(event.target.value);
                    invalidate();
                  }}
                  className={FIELD_CONTROL}
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
                  {...props}
                  value={modelId}
                  onChange={(event) => {
                    setModelId(event.target.value);
                    invalidate();
                  }}
                  placeholder={
                    provider.id === "anthropic"
                      ? "claude-sonnet-5"
                      : "test the connection to list models"
                  }
                  className={FIELD_CONTROL}
                />
              )
            }
          </Field>
          {result?.ok && !result.tool_calling && modelId && (
            <p className="text-label text-ink-faint">
              Run the test again after choosing a model — Argus checks that it can use your notes.
            </p>
          )}
        </fieldset>

        {/* 4 — name */}
        <fieldset className="mb-4">
          <legend className={LEGEND}>4 · name it</legend>
          <Field label="Display name" visuallyHiddenLabel>
            {(props) => (
              <input
                {...props}
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                  setSaveError(null);
                }}
                placeholder="what you'll see in the model menu, e.g. groq-llama"
                className={FIELD_CONTROL}
              />
            )}
          </Field>
        </fieldset>

        {saveError && (
          <p role="alert" className="mb-3 border border-danger px-3 py-2 text-label text-danger">
            {saveError}
          </p>
        )}

        <div className="flex items-center gap-2 border-t border-line pt-4">
          <Button size="md" onClick={onClose}>
            CANCEL
          </Button>
          <Button
            type="submit"
            size="md"
            variant="primary"
            disabled={!canSave || saving}
            className="ml-auto"
          >
            {saving ? "SAVING…" : "SAVE MODEL"}
          </Button>
        </div>
        {!canSave && (
          <p className="mt-2 text-right text-meta text-ink-faint">
            {provider.id === "anthropic"
              ? "Enter a model name and a display name."
              : "Pass the connection test, then name it."}
          </p>
        )}
      </form>
    </Dialog>
  );
}
