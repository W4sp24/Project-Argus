"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import Stepper from "@/components/ui/Stepper";
import { addModel, testModel, type ModelProvider, type TestModelResult } from "@/lib/api";

/**
 * Guided model setup (§7), as a stepped wizard.
 *
 * This was one ~620px scrolling form with every field for every provider
 * rendered at once — the first thing a new user meets, and a wall. It is now
 * one decision per screen, and the steps themselves depend on the provider:
 * the Claude Code path has nothing to connect, so it never shows a connection
 * step it would only have to explain away.
 *
 * The test-before-save gate is the point, and it is now the *step* gate. Two
 * rounds, mapped onto the two steps that need them:
 *  - CONNECT tests reachability with no model id — the backend answers
 *    "reachable, now choose a model" and hands back what the endpoint serves,
 *    so the model becomes a dropdown rather than a guess.
 *  - MODEL verifies the chosen model can actually call tools. Argus has no
 *    no-tools fallback, so a model that cannot would silently break citations
 *    (I6) and planner proposals (I1).
 *
 * Editing anything upstream un-proves everything downstream (see `invalidate`)
 * — otherwise someone could test one configuration and save a different one.
 */

interface ProviderOption {
  id: ModelProvider;
  glyph: string;
  title: string;
  blurb: string;
  privacy: string;
  local: boolean;
  needsEndpoint: boolean;
  needsKey: boolean;
  defaultEndpoint?: string;
  /** Shown under a failed connection test — the thing that is usually wrong. */
  failureHint: string;
}

const PROVIDERS: ProviderOption[] = [
  {
    id: "openai-compat",
    glyph: "▣",
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
    failureHint: "Is Ollama running? Start it, then test again.",
  },
  {
    id: "openai-compat",
    glyph: "◈",
    title: "Hosted API",
    blurb: "Groq, DeepSeek, Together, OpenRouter, and similar.",
    privacy: "Note excerpts are sent to that company's servers.",
    local: false,
    needsEndpoint: true,
    needsKey: true,
    failureHint: "Check the address ends in /v1 and the key is still valid.",
  },
  {
    id: "gemini",
    glyph: "◐",
    title: "Google Gemini",
    blurb: "Gemini models on a Google AI Studio key.",
    privacy: "Note excerpts are sent to Google.",
    local: false,
    needsEndpoint: false,
    needsKey: true,
    failureHint: "Get a key at aistudio.google.com and check it is enabled for the API.",
  },
  {
    id: "anthropic-api",
    glyph: "◆",
    title: "Anthropic API key",
    blurb: "Claude, billed to your API account. No Claude Code needed.",
    privacy: "Note excerpts are sent to Anthropic.",
    local: false,
    needsEndpoint: false,
    needsKey: true,
    failureHint: "Keys start with sk-ant- and the account needs credit.",
  },
  {
    id: "anthropic",
    glyph: "✦",
    title: "Claude Code",
    blurb: "Claude on your existing subscription. Needs Claude Code installed.",
    privacy: "Note excerpts are sent to Anthropic.",
    local: false,
    needsEndpoint: false,
    needsKey: false,
    failureHint: "Is the Claude Code CLI installed and signed in?",
  },
];

/** Base URLs for the hosted providers people actually use, so nobody hunts for them. */
const HOSTED_PRESETS: { label: string; endpoint: string }[] = [
  { label: "Groq", endpoint: "https://api.groq.com/openai/v1" },
  // deepseek-chat calls tools; deepseek-reasoner does not, and the
  // registration probe rejects it — which is the right outcome, but only
  // legible if the model step says so first.
  { label: "DeepSeek", endpoint: "https://api.deepseek.com/v1" },
  { label: "Together", endpoint: "https://api.together.xyz/v1" },
  { label: "Fireworks", endpoint: "https://api.fireworks.ai/inference/v1" },
  { label: "OpenRouter", endpoint: "https://openrouter.ai/api/v1" },
  { label: "DeepInfra", endpoint: "https://api.deepinfra.com/v1/openai" },
];

type StepKey = "where" | "connect" | "model" | "name";

const STEP_LABEL: Record<StepKey, string> = {
  where: "WHERE",
  connect: "CONNECT",
  model: "MODEL",
  name: "NAME",
};

/** A pass/fail block with room for the reason — not a one-line string. */
function TestOutcome({ result, hint }: { result: TestModelResult; hint: string }) {
  return (
    <div
      role="status"
      className={`border px-3 py-2 ${
        result.ok ? "border-ok bg-[rgba(52,211,153,0.06)]" : "border-danger bg-[rgba(251,113,133,0.06)]"
      }`}
    >
      <p className={`text-label leading-relaxed ${result.ok ? "text-ok" : "text-danger"}`}>
        <span aria-hidden className="mr-1 font-mono">
          {result.ok ? "✓" : "✗"}
        </span>
        {result.detail}
      </p>
      <p className="mt-1 font-mono text-meta text-ink-faint">
        {result.ok
          ? [
              result.latency_ms > 0 ? `${result.latency_ms}ms` : null,
              result.tool_calling ? "tool calling ok" : null,
              result.available_models.length > 0
                ? `${result.available_models.length} model${result.available_models.length === 1 ? "" : "s"} available`
                : null,
            ]
              .filter(Boolean)
              .join(" · ")
          : hint}
      </p>
    </div>
  );
}

export default function AddModelDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: () => void;
}) {
  const { show } = useToast();
  const [choice, setChoice] = useState(0);
  const [step, setStep] = useState(0);
  const [furthest, setFurthest] = useState(0);
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState(PROVIDERS[0].defaultEndpoint ?? "");
  const [apiKey, setApiKey] = useState("");
  const [modelId, setModelId] = useState("");
  /** Round one: is the endpoint there at all, and what does it serve? */
  const [reach, setReach] = useState<TestModelResult | null>(null);
  /** Round two: can the chosen model actually call tools? */
  const [verified, setVerified] = useState<TestModelResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const provider = PROVIDERS[choice];
  const skipsConnect = provider.id === "anthropic";

  const stepKeys = useMemo<StepKey[]>(
    () => (skipsConnect ? ["where", "model", "name"] : ["where", "connect", "model", "name"]),
    [skipsConnect],
  );
  const stepKey = stepKeys[Math.min(step, stepKeys.length - 1)];

  const bodyRef = useRef<HTMLDivElement>(null);
  const mounted = useRef(false);

  // Each step owns the keyboard when it opens. Dialog already places focus on
  // mount, so this only runs on the transitions after it.
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

  /** A changed connection un-proves the endpoint *and* the model behind it. */
  function invalidateConnection() {
    setReach(null);
    setVerified(null);
    setSaveError(null);
    const connectAt = stepKeys.indexOf("connect");
    if (connectAt >= 0) setFurthest((far) => Math.min(far, connectAt));
  }

  /** A changed model leaves the endpoint proven but the model unproven. */
  function invalidateModel() {
    setVerified(null);
    setSaveError(null);
    setFurthest((far) => Math.min(far, stepKeys.indexOf("model")));
  }

  function pickProvider(index: number) {
    if (index !== choice) {
      setChoice(index);
      setEndpoint(PROVIDERS[index].defaultEndpoint ?? "");
      setApiKey("");
      setModelId("");
      setReach(null);
      setVerified(null);
      setSaveError(null);
      // A different provider has a different step list and none of the old
      // proof carries over, so progress resets rather than pointing at a step
      // that may no longer exist.
      setFurthest(1);
    } else {
      setFurthest((far) => Math.max(far, 1));
    }
    // Choosing is the answer to this step — no separate NEXT to hunt for.
    setStep(1);
  }

  const canTestConnection = useMemo(() => {
    if (provider.needsEndpoint && !endpoint.trim()) return false;
    if (provider.needsKey && !apiKey.trim()) return false;
    return true;
  }, [provider, endpoint, apiKey]);

  // Unchanged from the single-page form: a model is proven only when a real
  // probe came back green, and Claude Code is the one exemption.
  const tested = verified?.ok === true && Boolean(modelId.trim());
  const canSave = Boolean(name.trim()) && (tested || skipsConnect);

  const canAdvance =
    stepKey === "where"
      ? true
      : stepKey === "connect"
        ? reach?.ok === true
        : stepKey === "model"
          ? skipsConnect
            ? Boolean(modelId.trim())
            : tested
          : false;

  async function runTest(withModel: boolean) {
    if (testing) return;
    setTesting(true);
    setSaveError(null);
    try {
      const outcome = await testModel({
        provider: provider.id,
        endpoint: provider.needsEndpoint ? endpoint.trim() : undefined,
        api_key: provider.needsKey ? apiKey.trim() : undefined,
        model_id: withModel ? modelId.trim() || undefined : undefined,
        name: name.trim() || undefined,
      });
      if (withModel) {
        setVerified(outcome);
        if (outcome.ok) setFurthest((far) => Math.max(far, stepKeys.indexOf("name")));
      } else {
        setReach(outcome);
        // The endpoint told us what it serves — offer the first one so a
        // non-developer never has to know a model id by heart.
        if (outcome.ok && !modelId.trim() && outcome.available_models.length > 0) {
          setModelId(outcome.available_models[0]);
        }
        if (outcome.ok) setFurthest((far) => Math.max(far, stepKeys.indexOf("model")));
      }
    } catch (error) {
      const failure: TestModelResult = {
        ok: false,
        detail: error instanceof Error ? error.message : "the test could not run",
        tool_calling: false,
        latency_ms: 0,
        available_models: [],
      };
      if (withModel) setVerified(failure);
      else setReach(failure);
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

  function advance() {
    if (!canAdvance) return;
    const next = Math.min(step + 1, stepKeys.length - 1);
    setStep(next);
    setFurthest((far) => Math.max(far, next));
  }

  const options = reach?.available_models ?? [];

  return (
    <Dialog
      label="Add a model"
      onClose={onClose}
      align="center"
      className="w-[40rem] max-w-[calc(100vw-2rem)]"
    >
      <header className="border-b border-line px-5 pb-4 pt-5">
        <p className="eyebrow">▍ADD.MODEL</p>
        <Stepper
          steps={stepKeys.map((key) => STEP_LABEL[key])}
          current={step}
          furthest={furthest}
          onJump={setStep}
          className="mt-3"
        />
      </header>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          // Enter does whatever this step is for: advance while there is a step
          // left, save on the last one.
          if (stepKey === "name") void save();
          else advance();
        }}
      >
        {/* A floor under the body so the footer does not walk up and down the
            screen as steps of different heights swap in. */}
        <div ref={bodyRef} className="min-h-[19rem] px-5 py-5">
          {stepKey === "where" && (
            <>
              <p className="mb-3 text-label text-ink-muted">Where should this model run?</p>
              <div className="grid gap-2 sm:grid-cols-2">
                {PROVIDERS.map((option, index) => {
                  const active = index === choice;
                  return (
                    <button
                      key={option.title}
                      type="button"
                      aria-pressed={active}
                      onClick={() => pickProvider(index)}
                      className={`group flex flex-col border p-3 text-left transition-colors ${
                        active
                          ? "border-[var(--ac)] bg-[var(--ac-bg)]"
                          : "border-line hover:border-lineHi"
                      }`}
                    >
                      <span className="flex items-center gap-2">
                        <span
                          aria-hidden
                          className={`font-mono text-lead leading-none transition-colors ${
                            active ? "text-[var(--ac)]" : "text-ink-faint"
                          }`}
                        >
                          {option.glyph}
                        </span>
                        <span
                          className={`ml-auto shrink-0 border px-1.5 py-px font-mono text-micro uppercase tracking-[0.14em] ${
                            option.local ? "border-ok text-ok" : "border-line text-ink-faint"
                          }`}
                        >
                          {option.local ? "LOCAL" : "HOSTED"}
                        </span>
                      </span>
                      <span className="mt-2 block truncate text-body text-ink">{option.title}</span>
                      <span className="mt-1 block text-label leading-relaxed text-ink-muted">
                        {option.blurb}
                      </span>
                      <span className="mt-auto block pt-2 text-meta leading-relaxed text-ink-faint">
                        {option.privacy}
                      </span>
                    </button>
                  );
                })}
              </div>
            </>
          )}

          {stepKey === "connect" && (
            <div className="flex flex-col gap-3">
              <p className="text-label text-ink-muted">
                {provider.local
                  ? "Argus will check the address on this machine before you continue."
                  : "Argus will check the address and key before you continue."}
              </p>

              {provider.needsEndpoint && (
                <>
                  {!provider.local && (
                    <div className="flex flex-wrap gap-1.5">
                      {HOSTED_PRESETS.map((preset) => (
                        <Button
                          key={preset.label}
                          variant={endpoint === preset.endpoint ? "primary" : "quiet"}
                          className="normal-case tracking-normal"
                          onClick={() => {
                            setEndpoint(preset.endpoint);
                            invalidateConnection();
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
                          invalidateConnection();
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
                        invalidateConnection();
                      }}
                      placeholder="pasted from your provider's dashboard"
                      className={FIELD_CONTROL}
                    />
                  )}
                </Field>
              )}

              <div>
                <Button
                  size="md"
                  variant={reach?.ok ? "secondary" : "primary"}
                  onClick={() => runTest(false)}
                  disabled={!canTestConnection || testing}
                >
                  {testing ? "TESTING…" : reach?.ok ? "TEST AGAIN" : "TEST CONNECTION"}
                </Button>
              </div>

              {reach && <TestOutcome result={reach} hint={provider.failureHint} />}
            </div>
          )}

          {stepKey === "model" && (
            <div className="flex flex-col gap-3">
              {skipsConnect ? (
                <p className="text-label leading-relaxed text-ink-muted">
                  Uses the Claude Code CLI you already signed in to. Nothing to configure — enter
                  the Claude model name, for example <code className="text-ink">claude-sonnet-5</code>.
                </p>
              ) : (
                <p className="text-label text-ink-muted">
                  {options.length > 0
                    ? `${endpoint.trim() || "That server"} serves ${options.length} model${options.length === 1 ? "" : "s"}. Pick one, then Argus checks it can use your notes.`
                    : "Enter the model id this server expects, then Argus checks it can use your notes."}
                </p>
              )}

              {/* The probe rejects a model that will not call tools, which is
                  correct but reads as "DeepSeek doesn't work" if you happened
                  to pick the reasoner. Say which one to pick first. */}
              {endpoint.includes("deepseek") && (
                <p className="text-label text-ink-faint">
                  Pick <code className="text-ink">deepseek-chat</code>.{" "}
                  <code className="text-ink">deepseek-reasoner</code> cannot call tools, so Argus
                  will refuse it — vault citations depend on them.
                </p>
              )}

              <Field label="Model" visuallyHiddenLabel>
                {(props) =>
                  options.length > 0 ? (
                    <select
                      {...props}
                      value={modelId}
                      onChange={(event) => {
                        setModelId(event.target.value);
                        invalidateModel();
                      }}
                      className={FIELD_CONTROL}
                    >
                      <option value="">choose a model…</option>
                      {options.map((available) => (
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
                        invalidateModel();
                      }}
                      placeholder={skipsConnect ? "claude-sonnet-5" : "the model id this server expects"}
                      className={FIELD_CONTROL}
                    />
                  )
                }
              </Field>

              {!skipsConnect && (
                <>
                  <div>
                    <Button
                      size="md"
                      variant={verified?.ok ? "secondary" : "primary"}
                      onClick={() => runTest(true)}
                      disabled={!modelId.trim() || testing}
                    >
                      {testing ? "CHECKING…" : verified?.ok ? "CHECK AGAIN" : "CHECK THIS MODEL"}
                    </Button>
                  </div>
                  {verified && <TestOutcome result={verified} hint={provider.failureHint} />}
                  {verified?.ok && !verified.tool_calling && (
                    <p className="text-label leading-relaxed text-ink-faint">
                      This model answered but did not use a tool. Argus needs tool calling to cite
                      your notes — expect citations to be missing.
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          {stepKey === "name" && (
            <div className="flex flex-col gap-4">
              <Field label="display name" hint="What you'll see in the model menu.">
                {(props) => (
                  <input
                    {...props}
                    value={name}
                    onChange={(event) => {
                      setName(event.target.value);
                      setSaveError(null);
                    }}
                    placeholder="e.g. groq-llama"
                    className={FIELD_CONTROL}
                  />
                )}
              </Field>

              <div className="border border-line bg-sunken px-3 py-2.5">
                <p className="mb-2 font-mono text-micro uppercase tracking-[0.14em] text-ink-faint">
                  about to save
                </p>
                <dl className="flex flex-col gap-1 font-mono text-meta">
                  <div className="flex gap-3">
                    <dt className="w-20 shrink-0 text-ink-faint">where</dt>
                    <dd className="min-w-0 flex-1 break-words text-ink-muted">{provider.title}</dd>
                  </div>
                  {provider.needsEndpoint && (
                    <div className="flex gap-3">
                      <dt className="w-20 shrink-0 text-ink-faint">address</dt>
                      <dd className="min-w-0 flex-1 break-all text-ink-muted">{endpoint.trim()}</dd>
                    </div>
                  )}
                  {provider.needsKey && (
                    <div className="flex gap-3">
                      <dt className="w-20 shrink-0 text-ink-faint">api key</dt>
                      <dd className="min-w-0 flex-1 text-ink-muted">
                        stored in your OS keyring, never in a file
                      </dd>
                    </div>
                  )}
                  <div className="flex gap-3">
                    <dt className="w-20 shrink-0 text-ink-faint">model</dt>
                    <dd className="min-w-0 flex-1 break-all text-ink-muted">
                      {modelId.trim() || "—"}
                    </dd>
                  </div>
                  <div className="flex gap-3">
                    <dt className="w-20 shrink-0 text-ink-faint">check</dt>
                    <dd className={`min-w-0 flex-1 ${tested ? "text-ok" : "text-ink-muted"}`}>
                      {tested
                        ? `verified${verified?.tool_calling ? " · tool calling ok" : ""}`
                        : "verified on save"}
                    </dd>
                  </div>
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
            {stepKey !== "name" && !canAdvance && (
              <p className="text-right text-meta text-ink-faint">
                {stepKey === "connect"
                  ? "Pass the connection test to continue."
                  : skipsConnect
                    ? "Enter a model name to continue."
                    : "Check the model to continue."}
              </p>
            )}
            {stepKey === "name" && !canSave && (
              <p className="text-right text-meta text-ink-faint">Give it a name to save.</p>
            )}
            {stepKey === "name" ? (
              <Button type="submit" size="md" variant="primary" disabled={!canSave || saving}>
                {saving ? "SAVING…" : "SAVE MODEL"}
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
