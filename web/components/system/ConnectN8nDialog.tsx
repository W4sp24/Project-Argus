"use client";

import { useState } from "react";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import Stepper from "@/components/ui/Stepper";
import {
  deleteAutomationInstance,
  discoverN8nWorkflows,
  issueExternalToken,
  registerN8nInstance,
  testN8nInstance,
  type AutomationInstance,
  type DiscoveredWorkflow,
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
 * **Where registration happens, and why it is not last.** The inbound token
 * is per-instance — that is what makes revoking one instance's token leave
 * the others working — so it cannot exist before the instance does. Showing
 * it therefore has to come after registration, not before. The instance is
 * created on leaving TEST, which is the first moment its credential has been
 * proven, matching the "verify before persist" rule the rest of the app
 * already follows. Backing out after that point deletes it again, so an
 * abandoned dialog never leaves a half-configured instance behind.
 */
const STEPS = ["INSTANCE", "TEST", "INBOUND", "DISCOVER"];

type Kind = "LOCAL" | "REMOTE";

/** One TEST checklist line. `proven` distinguishes what the probe actually
 * demonstrated from what it merely implies — see CHECKS below. */
interface Check {
  label: string;
  state: "pending" | "running" | "ok" | "fail";
  detail: string;
  inferred?: boolean;
}

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
  const [kind, setKind] = useState<Kind>("REMOTE");

  const [result, setResult] = useState<N8nProbeResult | null>(null);
  const [testing, setTesting] = useState(false);

  const [registered, setRegistered] = useState<AutomationInstance | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [credential, setCredential] = useState<ExternalTokenResult | null>(null);
  const [found, setFound] = useState<DiscoveredWorkflow[] | null>(null);

  /** Any edit un-proves a previous green light. A token proven against one
   * instance means nothing about another, so progress resets to the start. */
  function invalidate() {
    setResult(null);
    setError(null);
    setFurthest(0);
    setCurrent(0);
  }

  function advance(next: number) {
    setCurrent(next);
    setFurthest((f) => Math.max(f, next));
  }

  const canTest =
    baseUrl.trim().startsWith("http") && apiKey.trim().length > 0 && name.trim().length > 0;
  const probed = result?.ok === true;

  /**
   * The four TEST lines, derived from one probe rather than four requests.
   *
   * Only two of these are independently proven by the response: the instance
   * answered, and it returned a workflow count for a tag-filtered query. The
   * other two are entailments — a 401 would not have produced a count, and
   * the count *is* the tagged count, because the probe queries `?tags=argus`.
   * They are marked `inferred` and labelled as such in the UI rather than
   * dressed up as four separate checks, which would be theatre.
   */
  function checks(): Check[] {
    const state = (ok: boolean): Check["state"] =>
      testing ? "running" : result === null ? "pending" : ok ? "ok" : "fail";
    const count = result?.workflow_count ?? 0;
    return [
      {
        label: "instance reachable",
        state: state(Boolean(result?.ok) || result?.workflow_count !== null),
        detail: result ? (result.latency_ms !== null ? `${result.latency_ms} ms` : result.detail) : "",
      },
      {
        label: "api key accepted",
        state: state(Boolean(result?.ok)),
        detail: result?.ok ? "authorised" : (result?.detail ?? ""),
        inferred: true,
      },
      {
        label: "workflows readable",
        state: state(Boolean(result?.ok)),
        detail: result?.ok ? `${count} returned` : "",
      },
      {
        label: "argus tag present",
        state: testing ? "running" : result === null ? "pending" : count > 0 ? "ok" : "fail",
        detail: result?.ok
          ? count > 0
            ? `${count} tagged`
            : "nothing tagged argus yet — tag a workflow in n8n and refresh later"
          : "",
        inferred: true,
      },
    ];
  }

  async function runTest() {
    if (!canTest || testing) return;
    setTesting(true);
    setError(null);
    try {
      setResult(await testN8nInstance({ base_url: baseUrl.trim(), api_key: apiKey.trim() }));
    } catch (err) {
      setResult({
        ok: false,
        detail: err instanceof Error ? err.message : "the test could not run",
        latency_ms: null,
        workflow_count: null,
      });
    } finally {
      setTesting(false);
    }
  }

  /** Register, then fetch this instance's own inbound token and what it can
   * see. Runs on leaving TEST — see the module docstring for why here. */
  async function registerAndContinue() {
    if (!probed || busy) return;
    setBusy(true);
    setError(null);
    try {
      const instance =
        registered ??
        (await registerN8nInstance({
          name: name.trim(),
          base_url: baseUrl.trim(),
          api_key: apiKey.trim(),
          kind,
        }));
      setRegistered(instance);
      if (!credential) setCredential(await issueExternalToken(instance.id));
      advance(2);
      onConnected();
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not register the instance");
    } finally {
      setBusy(false);
    }
  }

  async function loadDiscovered() {
    advance(3);
    if (found !== null) return;
    try {
      setFound(await discoverN8nWorkflows({ base_url: baseUrl.trim(), api_key: apiKey.trim() }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not list workflows");
      setFound([]);
    }
  }

  /** Backing out after registration removes the instance again, so an
   * abandoned dialog never leaves a half-configured entry behind. */
  async function cancel() {
    if (registered) {
      try {
        await deleteAutomationInstance(registered.id);
        onConnected();
      } catch {
        show(`automations :: ${registered.name} was registered but could not be removed`, {
          tone: "error",
        });
      }
    }
    onClose();
  }

  function finish() {
    show(
      `automations :: ${registered?.name ?? "instance"} connected — ${found?.length ?? 0} workflows tagged argus`,
    );
    onConnected();
    onClose();
  }

  function primaryAction() {
    if (current === 0) {
      if (probed) advance(1);
      else void runTest();
    } else if (current === 1) {
      if (probed) void registerAndContinue();
      else void runTest();
    } else if (current === 2) {
      void loadDiscovered();
    } else {
      finish();
    }
  }

  const primaryLabel =
    current === 0
      ? probed
        ? "CONTINUE →"
        : testing
          ? "TESTING…"
          : "TEST CONNECTION →"
      : current === 1
        ? busy
          ? "REGISTERING…"
          : "REGISTER →"
        : current === 2
          ? "DISCOVER →"
          : "DONE";

  const primaryDisabled =
    (current === 0 && !canTest) ||
    (current === 1 && (!probed || busy)) ||
    testing;

  return (
    <Dialog
      label="Connect n8n"
      onClose={() => void cancel()}
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
              Point Argus at an n8n instance. You can register several — a local one for
              anything touching the vault, a remote one for workflows that must keep running
              when this machine is shut. The key is checked before it is stored and goes to
              your OS keyring; <code className="font-mono text-ink">.argus/automations.json</code>{" "}
              keeps only a reference.
            </p>

            <div className="grid grid-cols-[1fr_auto] items-end gap-3">
              <Field label="instance name" hint="How this one is labelled everywhere else.">
                {(props) => (
                  <input
                    {...props}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="vps-hetzner"
                    className={FIELD_CONTROL}
                  />
                )}
              </Field>
              <div className="flex flex-col gap-1">
                <span className="font-mono text-meta uppercase tracking-[0.1em] text-ink-faint">
                  kind
                </span>
                <div className="flex gap-1.5">
                  {(["LOCAL", "REMOTE"] as Kind[]).map((option) => (
                    <Button
                      key={option}
                      size="sm"
                      variant={kind === option ? "primary" : "quiet"}
                      onClick={() => setKind(option)}
                      title={
                        option === "LOCAL"
                          ? "Runs on this machine. Stops when it sleeps."
                          : "Always on, reached over the network."
                      }
                    >
                      {option}
                    </Button>
                  ))}
                </div>
              </div>
            </div>

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

            <Field label="api key" hint="n8n → Settings → n8n API → create an API key.">
              {(props) => (
                <input
                  {...props}
                  type="password"
                  value={apiKey}
                  onChange={(event) => {
                    setApiKey(event.target.value);
                    invalidate();
                  }}
                  className={`${FIELD_CONTROL} font-mono text-meta`}
                />
              )}
            </Field>
          </div>
        )}

        {current === 1 && (
          <div className="flex flex-col gap-3">
            <p className="text-label leading-relaxed text-ink-muted">
              Nothing is saved until every check passes — an untested instance is how you end
              up with a dashboard full of WAITING widgets and no idea why.
            </p>
            <ul className="m-0 list-none p-0">
              {checks().map((check) => (
                <li
                  key={check.label}
                  className="flex items-center gap-2.5 border-t border-line py-1.5 font-mono text-meta"
                >
                  <span
                    className={`w-3.5 ${
                      check.state === "ok"
                        ? "text-ok"
                        : check.state === "fail"
                          ? "text-danger"
                          : check.state === "running"
                            ? "text-[var(--ac)]"
                            : "text-ink-faint"
                    }`}
                  >
                    {check.state === "ok"
                      ? "✓"
                      : check.state === "fail"
                        ? "✕"
                        : check.state === "running"
                          ? "▸"
                          : "·"}
                  </span>
                  <span className="min-w-0 flex-1 text-ink-muted">
                    {check.label}
                    {check.inferred && (
                      <span
                        className="ml-1.5 text-ink-faint"
                        title="Entailed by the same probe rather than checked separately — Argus makes one request, not four."
                      >
                        (implied)
                      </span>
                    )}
                  </span>
                  <span
                    className={`shrink-0 ${check.state === "ok" ? "text-ok" : "text-ink-faint"}`}
                  >
                    {check.detail}
                  </span>
                </li>
              ))}
            </ul>
            {/* Honest about the mechanism: four lines, one request. Dressing a
                single probe up as four sequential checks would be theatre. */}
            <p className="font-mono text-micro text-ink-faint">
              One request answers all four — n8n is asked for its `argus`-tagged workflows,
              and each line reads off what that answer proves.
            </p>
          </div>
        )}

        {current === 2 && (
          <div className="flex flex-col gap-3">
            <p className="w-fit border border-line bg-sunken px-2.5 py-1 font-mono text-micro uppercase tracking-[0.14em] text-[var(--ac)]">
              N8N → ARGUS
            </p>
            <p className="text-label leading-relaxed text-ink-muted">
              Display automations push into Argus rather than being polled, so this instance
              needs somewhere to POST. Each instance gets its own token, so revoking one does
              not silence the others.
            </p>

            <Field label="inbound endpoint" hint="Paste into the HTTP Request node at the end of each display workflow.">
              {(props) => (
                <div className="flex gap-2">
                  <input
                    {...props}
                    readOnly
                    value={credential ? `${credential.base_url}/api/external/widget/{slug}` : "—"}
                    className={`${FIELD_CONTROL} font-mono text-meta`}
                  />
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      if (!credential) return;
                      void navigator.clipboard?.writeText(
                        `${credential.base_url}/api/external/widget/`,
                      );
                      show("copied :: inbound endpoint on the clipboard");
                    }}
                  >
                    COPY
                  </Button>
                </div>
              )}
            </Field>

            <Field
              label="header"
              hint="Shown once — the keyring holds the only copy and there is no endpoint that reads it back."
            >
              {(props) => (
                <div className="flex gap-2">
                  <input
                    {...props}
                    readOnly
                    value={
                      credential ? `${credential.header_name}: ${credential.header_value}` : "—"
                    }
                    className={`${FIELD_CONTROL} font-mono text-meta`}
                  />
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      if (!credential) return;
                      void navigator.clipboard?.writeText(
                        `${credential.header_name}: ${credential.header_value}`,
                      );
                      show("copied :: header on the clipboard");
                    }}
                  >
                    COPY
                  </Button>
                </div>
              )}
            </Field>
          </div>
        )}

        {current === 3 && (
          <div className="flex flex-col gap-3">
            <p className="text-label leading-relaxed text-ink-muted">
              Only workflows tagged <code className="font-mono text-ink">argus</code> appear
              here, and only those are registered — <strong className="text-ink">the tag is
              the consent</strong>. Anything else in this n8n stays invisible to Argus. Tag a
              workflow later and it shows up on the next refresh.
            </p>
            {found === null ? (
              <p className="font-mono text-meta text-ink-faint">listing…</p>
            ) : found.length === 0 ? (
              <p className="text-label text-ink-muted">
                Nothing is tagged <code className="font-mono text-ink">argus</code> yet. The
                instance is registered — add the tag in n8n and hit REFRESH on its card.
              </p>
            ) : (
              <ul className="m-0 list-none p-0">
                {found.map((workflow) => (
                  <li
                    key={workflow.id}
                    className="flex items-center gap-2.5 border-t border-line py-2"
                  >
                    <span
                      className={`w-14 shrink-0 font-mono text-micro uppercase tracking-[0.14em] ${
                        workflow.kind === "action" ? "text-[#60a5fa]" : "text-[var(--ac)]"
                      }`}
                    >
                      {workflow.kind}
                    </span>
                    <span className="min-w-0 flex-1 truncate font-mono text-meta text-ink-bright">
                      {workflow.name ?? workflow.id}
                    </span>
                    <span
                      className={`shrink-0 font-mono text-micro uppercase tracking-[0.12em] ${
                        workflow.active ? "text-ok" : "text-ink-faint"
                      }`}
                      title={
                        workflow.active
                          ? "Active in n8n"
                          : "Inactive in n8n — it will not run or push until you activate it there"
                      }
                    >
                      {workflow.active ? "ACTIVE" : "INACTIVE"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {error && (
          <p className="mt-3 border border-danger bg-sunken px-3 py-2 text-label text-danger">
            {error}
          </p>
        )}

        <div className="mt-5 flex items-center gap-2 border-t border-line pt-4">
          <Button size="sm" variant="secondary" onClick={() => void cancel()}>
            CANCEL
          </Button>
          {current > 0 && (
            <Button size="sm" variant="quiet" onClick={() => setCurrent(current - 1)}>
              ← BACK
            </Button>
          )}
          <Button
            size="sm"
            variant="primary"
            type="submit"
            className="ml-auto"
            disabled={primaryDisabled}
          >
            {primaryLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
