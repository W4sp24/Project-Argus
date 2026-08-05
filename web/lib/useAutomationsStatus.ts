"use client";

import { useAutomationRuns, useAutomationWidgets, useAutomations } from "@/lib/api";
import { formatRelativeTime } from "@/lib/relativeTime";

/**
 * The ambient automations readout — the JARVIS-shaped part of this design.
 *
 * A persistent line reporting what fired, what failed, what went quiet and
 * whether the inbound surface is reachable, **without you navigating
 * anywhere**. That last part is the whole point: a push model fails silently,
 * so the failure has to come to you. If you have to open /automations to
 * discover a source died, you will discover it a week late.
 *
 * Returns `null` when there is nothing to say, so the caller keeps its
 * existing health line rather than showing an empty segment. An install with
 * no automations should look exactly as it did before this feature existed.
 */
export function useAutomationsStatus(): string | null {
  const { data: automations } = useAutomations();
  const { data: widgets } = useAutomationWidgets();
  const { data: runs } = useAutomationRuns();

  const registered = automations?.instance != null;
  if (!registered && !(widgets && widgets.length)) return null;

  const segments: string[] = [];

  // Reachability first: when n8n is unreachable everything else below is
  // reporting on stale information, and saying so up front stops the rest of
  // the line from being read as current.
  if (registered && automations && !automations.connected) {
    segments.push("n8n unreachable");
  }

  const stale = (widgets ?? []).filter((widget) => widget.state === "stale");
  if (stale.length === 1) {
    segments.push(`${stale[0].slug} stale`);
  } else if (stale.length > 1) {
    segments.push(`${stale.length} sources stale`);
  }

  const waiting = (widgets ?? []).filter((widget) => widget.state === "waiting");
  if (waiting.length > 0) segments.push(`${waiting.length} waiting`);

  // The most recent failure, named. It moves on — the ACTIVITY tab is where a
  // failure stays findable afterwards — but it should not pass unmentioned.
  const lastFailure = (runs ?? []).find(
    (run) => run.status === "failed" || run.status === "timeout",
  );
  if (lastFailure) {
    const when = lastFailure.finished_at ?? lastFailure.started_at;
    segments.push(
      `${lastFailure.workflow_name ?? "a run"} ${lastFailure.status}` +
        (when ? ` ${formatRelativeTime(when)}` : ""),
    );
  } else {
    const lastOk = (runs ?? []).find((run) => run.status === "ok");
    if (lastOk?.started_at) {
      segments.push(`last run ${formatRelativeTime(lastOk.started_at)}`);
    }
  }

  if (segments.length === 0) segments.push("automations OK");
  return segments.join(" · ");
}
