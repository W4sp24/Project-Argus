"use client";

import { useState } from "react";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import Stepper from "@/components/ui/Stepper";
import {
  issueExternalToken,
  registerN8nInstance,
  testN8nInstance,
  type ExternalTokenResult,
  type N8nProbeResult,
} from "@/lib/api";

/**
 * Register an n8n instance.
 *
 * The one thing this dialog exists to get right: two credentials flow in
 * opposite directions, and nothing about their names or shapes (both are
 * "just a token") stops them from being pasted into the wrong box. n8n's API
 * key (ARGUS → N8N) lets Argus call n8n's REST API to discover and run
 * workflows tagged `argus`. Argus's own bearer token (N8N → ARGUS) is what a
 * workflow presents when it calls back into Argus — an `argus:async`
 * result, a dashboard widget push. Every step that touches a credential is
 * labelled with which direction it flows, so the two are never adjacent
 * without a label between them.
 *
 * Modelled on AddMcpServerDialog's test-before-save gate: TEST CONNECTION
 * must return ok before the flow can advance past step 1 (`Stepper`'s
 * `furthest` enforces this — a step that is still unproven cannot be
 * clicked past), and any edit to the base URL or key un-proves a previous
 * green light. The proof shown in step 2 is the workflow count, not a
 * checkmark, for the same reason AddMcpServerDialog shows a tool list.
 *
 * Step 3 (the N8N → ARGUS credential) issues that token on demand rather than
 * on open. Issuing rotates, and rotating invalidates the old value
 * immediately — so opening this dialog just to re-read the n8n URL must not
 * silently break every workflow already calling back with the previous token.
 * The value is shown once, because the keyring holds the only copy and there
 * is no endpoint that reads it back.
 */
const STEPS = ["CONNECT", "VERIFY", "CALLBACK", "SAVE"];

export default function ConnectN8nDialog({
  onClose,
  onConnected,
}: {
  onClose: () => void;
  onConnected: () => void;
}) {
  const { show } = useToast();
  const [current, setCurrent] = useState(0);
  const [furthest, setFurthest] = useState(0);

  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [name, setName] = useState("");
  // The N8N -> ARGUS credential. Issued on demand rather than on open,
  // because issuing rotates: opening this dialog to re-read the n8n URL
  // must not silently invalidate a token that is already in use.
  const [credential, setCredential] = useState<ExternalTokenResult | null>(null);
  const [issuing, setIssuing] = useState(false);
  const [tokenError, setTokenError] = useState<string | null>(null);

  const [result, setResult] = useState<N8nProbeResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  /** Any edit un-proves a previous green light — back to "must test again". */
  function invalidate() {
    setResult(null);
    setSaveError(null);
    setFurthest(0);
    setCurrent(0);
  }

  function advance(next: number) {
    setCurrent(next);
    setFurthest((f) => Math.max(f, next));
  }

  const canTest = baseUrl.trim().startsWith("http") && apiKey.trim().length > 0;
  const canLeaveStep0 = result?.ok === true;
  const canSave = Boolean(name.trim()) && result?.ok === true;

  async function runTest() {
    if (!canTest || testing) return;
    setTesting(true);
    setSaveError(null);
    try {
      setResult(await testN8nInstance({ base_url: baseUrl.trim(), api_key: apiKey.trim() }));
    } catch (error) {
      setResult({
        ok: false,
        detail: error instanceof Error ? error.message : "the test could not run",
        latency_ms: null,
        workflow_count: null,
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
      await registerN8nInstance({
        name: name.trim(),
        base_url: baseUrl.trim(),
        api_key: apiKey.trim(),
      });
      show(`automations :: connected — ${result?.workflow_count ?? 0} workflows`);
      onConnected();
      onClose();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "could not connect to n8n");
    } finally {
      setSaving(false);
    }
  }

  function primaryAction() {
    if (current === 0) {
      if (canLeaveStep0) advance(1);
      else void runTest();
    } else if (current < STEPS.length - 1) {
      advance(current + 1);
    } else {
      void save();
    }
  }

  return (
    <Dialog
      label="Connect n8n"
      onClose={onClose}
      align="center"
      className="w-[38rem] max-w-[calc(100vw-2rem)]"
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          primaryAction();
        }}
        className="p-5"
      >
        <p className="eyebrow mb-3">▍CONNECT.N8N</p>

        <Stepper
          steps={STEPS}
          current={current}
          furthest={furthest}
          onJump={setCurrent}
          className="mb-5"
        />

        {current === 0 && (
          <div className="flex flex-col gap-3">
            <p className="w-fit border border-line bg-sunken px-2.5 py-1 font-mono text-micro uppercase tracking-[0.14em] text-[var(--ac)]">
              ARGUS → N8N
            </p>
            <p className="text-label leading-relaxed text-ink-muted">
              n8n&rsquo;s API key lets Argus call n8n to discover and run workflows tagged{" "}
              <code className="font-mono text-ink">argus</code>.
            </p>

            <Field
              label="n8n base url"
              hint="Where your n8n instance is reachable from this machine."
            >
              {(props) => (
                <input
                  {...props}
                  value={baseUrl}
                  onChange={(event) => {
                    setBaseUrl(event.target.value);
                    invalidate();
                  }}
                  placeholder="http://localhost:5678"
                  className={`${FIELD_CONTROL} font-mono text-meta`}
                />
              )}
            </Field>

            <Field
              label="n8n api key"
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
                  placeholder="n8n api key"
                  className={FIELD_CONTROL}
                />
              )}
            </Field>

            <div>
              <Button
                size="md"
                variant={result?.ok ? "secondary" : "primary"}
                onClick={() => void runTest()}
                disabled={!canTest || testing}
              >
                {testing ? "CONNECTING…" : result?.ok ? "TEST AGAIN" : "TEST CONNECTION"}
              </Button>
            </div>

            {result && (
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
                  {result.ok && result.latency_ms !== null && (
                    <span className="text-ink-faint"> · {result.latency_ms}ms</span>
                  )}
                </p>
              </div>
            )}
          </div>
        )}

        {current === 1 && (
          <div className="flex flex-col gap-3">
            <p className="w-fit border border-line bg-sunken px-2.5 py-1 font-mono text-micro uppercase tracking-[0.14em] text-[var(--ac)]">
              ARGUS → N8N
            </p>
            <p className="text-label text-ink-muted">
              The connection is proven — this is what it found, not a checkmark.
            </p>
            <div role="status" className="border border-ok bg-[rgba(52,211,153,0.06)] px-3 py-3">
              <p className="font-mono text-title text-ink-bright">
                {result?.workflow_count ?? 0}
                <span className="ml-2 text-label font-normal text-ink-muted">
                  workflow{result?.workflow_count === 1 ? "" : "s"} tagged{" "}
                  <code className="font-mono">argus</code>
                </span>
              </p>
              {result?.latency_ms !== null && result?.latency_ms !== undefined && (
                <p className="mt-1 text-meta text-ink-faint">{result.latency_ms}ms round trip</p>
              )}
            </div>
          </div>
        )}

        {current === 2 && (
          <div className="flex flex-col gap-3">
            <p className="w-fit border border-line bg-sunken px-2.5 py-1 font-mono text-micro uppercase tracking-[0.14em] text-[var(--ac)]">
              N8N → ARGUS
            </p>
            <p className="text-label leading-relaxed text-ink-muted">
              The other direction: a workflow calls back into Argus for two things — an{" "}
              <code className="font-mono text-ink">argus:async</code> workflow&rsquo;s real result,
              and any dashboard widget it pushes. That call carries Argus&rsquo;s own bearer
              token, stored in your operating system&rsquo;s keyring and never in a file.
            </p>
            {credential === null ? (
              <div className="flex flex-col gap-2">
                <p className="text-label leading-relaxed text-ink-faint">
                  Workflows that never call back — a plain form or button trigger — don&rsquo;t
                  need this at all, and connecting n8n works either way.
                </p>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setTokenError(null);
                    setIssuing(true);
                    issueExternalToken()
                      .then(setCredential)
                      .catch((error: unknown) =>
                        setTokenError(
                          error instanceof Error ? error.message : "could not issue a token",
                        ),
                      )
                      .finally(() => setIssuing(false));
                  }}
                  disabled={issuing}
                >
                  {issuing ? "ISSUING…" : "ISSUE TOKEN"}
                </Button>
                {tokenError && (
                  <p role="alert" className="border border-danger px-3 py-2 text-label text-danger">
                    {tokenError}
                  </p>
                )}
              </div>
            ) : (
              <div role="status" className="flex flex-col gap-2 border border-ok bg-[rgba(52,211,153,0.06)] px-3 py-3">
                <p className="text-label text-ink-muted">
                  Create a <span className="text-ink">Header Auth</span> credential in n8n with
                  these two values. Copy it now — Argus keeps only the keyring&rsquo;s copy and
                  cannot show it again.
                </p>
                <dl className="flex flex-col gap-1.5">
                  <div>
                    <dt className="font-mono text-micro uppercase tracking-[0.14em] text-ink-faint">
                      name
                    </dt>
                    <dd className="break-all font-mono text-label text-ink-bright">
                      {credential.header_name}
                    </dd>
                  </div>
                  <div>
                    <dt className="font-mono text-micro uppercase tracking-[0.14em] text-ink-faint">
                      value
                    </dt>
                    <dd className="break-all font-mono text-label text-ink-bright">
                      {credential.header_value}
                    </dd>
                  </div>
                </dl>
                {credential.rotated && (
                  <p className="text-meta text-ink-faint">
                    This replaced an existing token. Any workflow still using the old one will
                    now be refused.
                  </p>
                )}
                {!credential.base_url && (
                  <p className="text-meta text-ink-faint">
                    No public URL is configured yet, so workflows have nowhere to send this. Set
                    one before installing templates.
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {current === 3 && (
          <div className="flex flex-col gap-3">
            <Field label="name" hint="What this instance is called in Argus.">
              {(props) => (
                <input
                  {...props}
                  value={name}
                  onChange={(event) => {
                    setName(event.target.value);
                    setSaveError(null);
                  }}
                  placeholder="e.g. home n8n"
                  className={FIELD_CONTROL}
                />
              )}
            </Field>

            {saveError && (
              <p role="alert" className="border border-danger px-3 py-2 text-label text-danger">
                {saveError}
              </p>
            )}
          </div>
        )}

        <div className="mt-5 flex items-center gap-2 border-t border-line pt-4">
          <Button size="md" onClick={onClose}>
            CANCEL
          </Button>
          {current > 0 && (
            <Button size="md" onClick={() => setCurrent(current - 1)}>
              BACK
            </Button>
          )}
          <div className="ml-auto flex items-center gap-3">
            {current === 0 && !canLeaveStep0 && (
              <p className="text-right text-meta text-ink-faint">
                Pass the connection test to continue.
              </p>
            )}
            {current < STEPS.length - 1 ? (
              <Button
                type="submit"
                size="md"
                variant="primary"
                disabled={current === 0 && !canLeaveStep0}
              >
                NEXT
              </Button>
            ) : (
              <Button type="submit" size="md" variant="primary" disabled={!canSave || saving}>
                {saving ? "SAVING…" : "SAVE"}
              </Button>
            )}
          </div>
        </div>
      </form>
    </Dialog>
  );
}
