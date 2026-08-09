"use client";

import Chart from "./widgets/Chart";
import List from "./widgets/List";
import Metric from "./widgets/Metric";
import Table from "./widgets/Table";
import Text from "./widgets/Text";
import Timeline from "./widgets/Timeline";

/**
 * Dispatches a pushed widget's `kind` to the renderer that knows its shape.
 * `kind` is free text on the wire (see `AutomationWidget.kind` in
 * `web/lib/api.ts`) — the backend only ever writes one of the six kinds
 * `validate_widget_payload` accepts, but an older cached row, a downgraded
 * build, or a future kind this build predates could still hand this an
 * unrecognised string. That renders an explicit, labeled fallback rather
 * than a blank panel a user has to debug from nothing.
 */
export default function WidgetRenderer({ kind, payload }: { kind: string; payload: unknown }) {
  switch (kind) {
    case "metric":
      return <Metric payload={payload} />;
    case "list":
      return <List payload={payload} />;
    case "table":
      return <Table payload={payload} />;
    case "timeline":
      return <Timeline payload={payload} />;
    case "text":
      return <Text payload={payload} />;
    case "chart":
      return <Chart payload={payload} />;
    default:
      return (
        <p className="text-label leading-relaxed text-ink-faint">
          Unknown widget kind <span className="font-mono text-ink-muted">{String(kind)}</span> —
          this build doesn&rsquo;t know how to render it.
        </p>
      );
  }
}

/**
 * Shared tone mapping for a payload's free-text `state` field (a metric's
 * own state, a list/table row's state). This is text a workflow author
 * typed, not a closed enum the backend validates — anything outside the
 * recognised set reads as neutral rather than guessing a color or throwing.
 */
export function stateToneClass(state: unknown): string {
  if (typeof state !== "string") return "text-ink-faint";
  const key = state.trim().toLowerCase();
  if (["ok", "good", "success", "live", "healthy", "done"].includes(key)) return "text-ok";
  if (["warn", "warning", "stale", "degraded", "pending"].includes(key)) return "text-warn";
  if (["danger", "fail", "failed", "error", "down", "overdue"].includes(key)) return "text-danger";
  return "text-ink-faint";
}
