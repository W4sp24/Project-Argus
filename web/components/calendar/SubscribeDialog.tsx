"use client";

import { useState } from "react";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import Stepper from "@/components/ui/Stepper";
import {
  addSubscription,
  probeSubscription,
  type CalendarInfo,
  type SubscriptionProbe,
} from "@/lib/calendar";

/** Swatches, so two subscribed calendars are told apart at chip size. Kept to
 *  hues that read on the void background; `""` means "use the mode accent". */
const COLORS = ["", "#22d3ee", "#34d399", "#fbbf24", "#fb7185", "#e879f9", "#60a5fa"];

const STEPS = ["FEED", "CONFIRM"];

/**
 * Subscribing to an .ics URL.
 *
 * Probe before persist, which is the repo's ordering rule for anything
 * credentialed and also the honest UI: a subscription that saves and then
 * turns out to be unreachable renders as an empty calendar, which looks
 * exactly like a working one that has nothing in it. The confirm step reports
 * what the feed actually contained, so "subscribed" means "we read it".
 *
 * The URL is a credential — Google's "secret address in iCal format" puts the
 * secret in the path — so it goes to the OS keyring and never comes back out
 * of the API. That is why there is no edit flow here: a subscription is added
 * and removed, never re-shown.
 */
export default function SubscribeDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: (calendar: CalendarInfo) => void;
}) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [color, setColor] = useState("");
  const [probe, setProbe] = useState<SubscriptionProbe | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const step = probe ? 1 : 0;
  // The probe route shares the subscribe body, so the name is required before
  // the feed is ever fetched — an empty one is a 422 about a missing field
  // rather than anything to do with the URL.
  const incomplete = name.trim() === "" || url.trim() === "";

  async function check(submitted: React.FormEvent) {
    submitted.preventDefault();
    if (incomplete || busy) return;
    setBusy(true);
    setError(null);
    try {
      setProbe(await probeSubscription({ name: name.trim(), url: url.trim() }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "could not read that feed");
    }
    setBusy(false);
  }

  async function subscribe() {
    setBusy(true);
    setError(null);
    try {
      onAdded(await addSubscription({ name: name.trim(), url: url.trim(), color }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "could not subscribe");
      setBusy(false);
    }
  }

  return (
    <Dialog
      label="Subscribe to a calendar"
      onClose={busy ? () => {} : onClose}
      className="w-[32rem] max-w-[calc(100vw-2rem)] p-5"
    >
      <p className="eyebrow mb-3">▍SUBSCRIBE</p>
      {/* `furthest` is the step reached, so CONFIRM is never clickable before
          a probe has cleared FEED, and clicking FEED from it is the same
          "back" the button below is. */}
      <Stepper
        steps={STEPS}
        current={step}
        furthest={step}
        onJump={() => setProbe(null)}
        className="mb-4"
      />

      {step === 0 ? (
        <form onSubmit={check}>
          <div className="flex flex-col gap-3">
            <Field label="Name" hint="What this calendar is called in Argus.">
              {(props) => (
                <input
                  {...props}
                  autoFocus
                  value={name}
                  onChange={(changed) => setName(changed.target.value)}
                  placeholder="Uni timetable"
                  className={FIELD_CONTROL}
                />
              )}
            </Field>

            <Field
              label="Feed URL"
              hint="An .ics address — Google's “secret address in iCal format”, or any webcal/https feed. Treat it as a password: it is stored in your OS keyring, never in the vault."
            >
              {(props) => (
                <input
                  {...props}
                  type="url"
                  value={url}
                  onChange={(changed) => setUrl(changed.target.value)}
                  placeholder="https://calendar.google.com/calendar/ical/…/basic.ics"
                  className={FIELD_CONTROL}
                />
              )}
            </Field>

            <fieldset>
              <legend className="mb-1 font-mono text-meta uppercase tracking-[0.1em] text-ink-faint">
                Colour
              </legend>
              <div className="flex flex-wrap gap-1.5">
                {COLORS.map((swatch) => (
                  <button
                    key={swatch || "accent"}
                    type="button"
                    onClick={() => setColor(swatch)}
                    aria-pressed={color === swatch}
                    aria-label={swatch ? `Colour ${swatch}` : "Mode accent"}
                    className={`h-6 w-6 border ${
                      color === swatch ? "border-lineHi" : "border-line"
                    }`}
                    style={{ background: swatch || "var(--ac)" }}
                  />
                ))}
              </div>
            </fieldset>
          </div>

          {error && (
            <p className="mt-3 font-mono text-meta text-danger" role="alert">
              {error}
            </p>
          )}

          <div className="mt-5 flex items-center gap-2 border-t border-line pt-4">
            <Button size="md" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button
              type="submit"
              size="md"
              variant="primary"
              disabled={incomplete || busy}
              className="ml-auto"
            >
              {busy ? "CHECKING…" : "CHECK FEED"}
            </Button>
          </div>
        </form>
      ) : (
        <>
          <p className="text-body text-ink">
            Found <span className="text-[var(--ac)]">{probe?.events ?? 0}</span>{" "}
            {probe?.events === 1 ? "event" : "events"} in {name.trim()}.
          </p>
          {probe && probe.skipped > 0 && (
            <p className="mt-1 text-label text-ink-muted">
              {probe.skipped} entries were skipped — an entry with no start date, or one this
              parser could not read. The rest come across.
            </p>
          )}
          <p className="mt-3 text-label text-ink-muted">
            Subscribed calendars are read-only: Argus syncs them hourly and shows them alongside
            your own events, but an .ics feed is one-way by protocol.
          </p>

          {error && (
            <p className="mt-3 font-mono text-meta text-danger" role="alert">
              {error}
            </p>
          )}

          <div className="mt-5 flex items-center gap-2 border-t border-line pt-4">
            <Button size="md" onClick={() => setProbe(null)} disabled={busy}>
              Back
            </Button>
            <Button size="md" variant="primary" onClick={subscribe} disabled={busy} className="ml-auto">
              {busy ? "SUBSCRIBING…" : "SUBSCRIBE"}
            </Button>
          </div>
        </>
      )}
    </Dialog>
  );
}
