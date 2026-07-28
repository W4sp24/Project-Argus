"use client";

import { useState } from "react";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import Stepper from "@/components/ui/Stepper";
import { connectGcal, connectTodoist, uploadGcalCredentials } from "@/lib/api";

/**
 * The connect flows for the first-party connectors.
 *
 * These used to be a button that showed a toast naming a terminal command, so
 * "connect Todoist" meant "leave the app, find your shell, and remember the
 * flag order". Both flows now finish where they started.
 *
 * Todoist is one field. Google Calendar genuinely is two steps — its OAuth
 * client file has to exist before a consent flow can run against it — so it
 * gets the same `Stepper` the model wizard uses rather than one form that
 * silently fails on step two.
 */

function TodoistFlow({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!token.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await connectTodoist(token.trim());
      onDone();
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "could not connect Todoist");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
      className="p-5"
    >
      <p className="eyebrow mb-3">▍CONNECT.TODOIST</p>
      <p className="mb-4 text-label leading-relaxed text-ink-muted">
        Todoist → Settings → Integrations → Developer → copy your API token.
      </p>

      <Field label="api token" hint="Checked against Todoist, then stored in your OS keyring.">
        {(props) => (
          <input
            {...props}
            type="password"
            value={token}
            onChange={(event) => {
              setToken(event.target.value);
              setError(null);
            }}
            placeholder="pasted from the Developer tab"
            className={FIELD_CONTROL}
          />
        )}
      </Field>

      {error && (
        <p role="alert" className="mt-3 border border-danger px-3 py-2 text-label text-danger">
          {error}
        </p>
      )}

      <div className="mt-5 flex items-center gap-2 border-t border-line pt-4">
        <Button size="md" onClick={onClose}>
          CANCEL
        </Button>
        <Button
          type="submit"
          size="md"
          variant="primary"
          disabled={!token.trim() || busy}
          className="ml-auto"
        >
          {busy ? "CHECKING…" : "CONNECT"}
        </Button>
      </div>
    </form>
  );
}

function GcalFlow({
  hasCredentials,
  onClose,
  onDone,
}: {
  hasCredentials: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  // Someone who already ran `argus connect gcal` has the client file; drop them
  // straight on the consent step rather than making them re-upload it.
  const [step, setStep] = useState(hasCredentials ? 1 : 0);
  const [furthest, setFurthest] = useState(hasCredentials ? 1 : 0);
  const [json, setJson] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function saveCredentials() {
    if (!json.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await uploadGcalCredentials(json.trim());
      onDone();
      setStep(1);
      setFurthest(1);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "could not save that file");
    } finally {
      setBusy(false);
    }
  }

  async function runConsent() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await connectGcal();
      onDone();
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "consent did not complete");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (step === 0) void saveCredentials();
        else void runConsent();
      }}
      className="p-5"
    >
      <p className="eyebrow mb-3">▍CONNECT.CALENDAR</p>
      <Stepper
        steps={["OAUTH CLIENT", "CONSENT"]}
        current={step}
        furthest={furthest}
        onJump={setStep}
        className="mb-4"
      />

      {step === 0 ? (
        <>
          <p className="mb-3 text-label leading-relaxed text-ink-muted">
            In Google Cloud Console, create an OAuth client of type{" "}
            <span className="text-ink">Desktop app</span>, download its JSON, and paste the whole
            file here. It identifies the app — it is not your calendar data, and the token it
            produces goes to your OS keyring.
          </p>
          <Field label="credentials.json" visuallyHiddenLabel>
            {(props) => (
              <textarea
                {...props}
                value={json}
                onChange={(event) => {
                  setJson(event.target.value);
                  setError(null);
                }}
                rows={7}
                placeholder='{"installed": {"client_id": "…", "client_secret": "…"}}'
                className={`${FIELD_CONTROL} resize-y font-mono text-meta`}
              />
            )}
          </Field>
        </>
      ) : (
        <p className="text-label leading-relaxed text-ink-muted">
          Continuing opens your browser for Google&apos;s consent screen. Approve the calendar
          scope there and this dialog finishes on its own. Argus only asks for{" "}
          <code className="text-ink">calendar.events</code>.
        </p>
      )}

      {error && (
        <p role="alert" className="mt-3 border border-danger px-3 py-2 text-label text-danger">
          {error}
        </p>
      )}

      <div className="mt-5 flex items-center gap-2 border-t border-line pt-4">
        <Button size="md" onClick={onClose}>
          CANCEL
        </Button>
        {step === 1 && !hasCredentials && (
          <Button size="md" onClick={() => setStep(0)}>
            ← BACK
          </Button>
        )}
        <Button
          type="submit"
          size="md"
          variant="primary"
          disabled={busy || (step === 0 && !json.trim())}
          className="ml-auto"
        >
          {busy
            ? step === 0
              ? "SAVING…"
              : "WAITING FOR YOUR BROWSER…"
            : step === 0
              ? "SAVE CLIENT →"
              : "OPEN CONSENT"}
        </Button>
      </div>
    </form>
  );
}

export default function ConnectDialog({
  connectorId,
  hasCredentials,
  onClose,
  onDone,
}: {
  connectorId: string;
  /** gcal only: whether the OAuth client file is already on disk. */
  hasCredentials: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const label = connectorId === "gcal" ? "Connect Google Calendar" : "Connect Todoist";
  return (
    <Dialog
      label={label}
      onClose={onClose}
      align="center"
      className="w-[34rem] max-w-[calc(100vw-2rem)]"
    >
      {connectorId === "gcal" ? (
        <GcalFlow hasCredentials={hasCredentials} onClose={onClose} onDone={onDone} />
      ) : (
        <TodoistFlow onClose={onClose} onDone={onDone} />
      )}
    </Dialog>
  );
}
