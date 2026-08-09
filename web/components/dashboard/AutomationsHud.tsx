"use client";

import { useRouter } from "next/navigation";

import Panel from "@/components/Panel";
import Button from "@/components/ui/Button";
import { useAutomationEvents } from "@/lib/api";
import { useAutomationsStatus } from "@/lib/useAutomationsStatus";

/**
 * The ambient automations readout, given a home on the dashboard.
 *
 * `useAutomationsStatus` already folds the state of every instance into one
 * line for the header's health string. This panel is the same fact with
 * somewhere to act on it: the most recent thing that happened, coloured by
 * whether it went wrong, and the two things you would want next — manage
 * them, or run one.
 *
 * A push model fails silently. Nobody calls anything; a panel just shows
 * yesterday's data. So the failure has to come to you — if you have to open
 * /automations to discover a source died, you will discover it a week late.
 *
 * Renders nothing when there is nothing to say, exactly like the status hook,
 * so an install with no automations looks as it did before this feature
 * existed. An empty panel announcing you have no automations is worse than
 * the space it takes.
 */
export default function AutomationsHud() {
  const router = useRouter();
  const status = useAutomationsStatus();
  const { data: events } = useAutomationEvents(undefined, undefined, 1);

  if (!status) return null;

  const latest = events?.[0];
  const bad = latest?.tag === "FAIL";

  return (
    <Panel label="AUTOMATIONS.HUD">
      <p className={`m-0 font-mono text-meta leading-relaxed ${bad ? "text-danger" : "text-ok"}`}>
        {`› ${status}`}
      </p>
      {latest && (
        <p className="mt-1 truncate font-mono text-micro text-ink-faint" title={latest.text}>
          {latest.subject ? `${latest.subject} · ` : ""}
          {latest.text}
        </p>
      )}
      <div className="mt-3 flex gap-2">
        <Button size="sm" variant="secondary" onClick={() => router.push("/automations")}>
          MANAGE →
        </Button>
        <Button
          size="sm"
          variant="primary"
          onClick={() =>
            // The palette owns running things; this is a pointer to it, not a
            // second way in. Matches the ⌘K the rest of the app teaches.
            window.dispatchEvent(
              new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true }),
            )
          }
        >
          RUN AN ACTION
        </Button>
      </div>
    </Panel>
  );
}
