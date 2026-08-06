"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import Button from "@/components/ui/Button";
import type { AutomationCard, AutomationInstance, AutomationWidget } from "@/lib/api";
import { openExternalUrl } from "@/lib/quickLinks";
import { formatRelativeTime } from "@/lib/relativeTime";
import { ActionStateBadge, AuthChip, KindChip, OriginChip, StateBadge, type ActionState } from "./chips";
import WidgetInspectDialog from "./WidgetInspectDialog";

/**
 * The ACTIVE tab: one unified `▍REGISTERED · N` list, replacing the old
 * split ACTIONS + DISPLAYS panels. Every row — display or action — carries
 * the same shape: a KindChip, a name, a detail line, provenance, a state
 * badge, and exactly one action button.
 */

const RUN_STATUS_STYLE: Record<string, string> = {
  ok: "text-ok",
  running: "text-warn",
  failed: "text-danger",
  timeout: "text-danger",
};

/** "every 15m" / "every 2h" — the pushed-widget cadence a workflow declared. */
function formatCadence(seconds: number | null): string | null {
  if (!seconds) return null;
  if (seconds < 60) return `every ${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `every ${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `every ${hours}h`;
  const days = Math.round(hours / 24);
  return `every ${days}d`;
}

/** Mirrors WidgetShell's workflowsUrl: a widget slug does not identify a
 * single workflow (the payload names the widget, not what pushed it), so
 * the honest OPEN IN N8N target is the instance's workflow list, never a
 * guessed deep link. */
function workflowsUrl(baseUrl: string | undefined): string | null {
  if (!baseUrl) return null;
  return `${baseUrl.replace(/\/+$/, "")}/home/workflows`;
}

function actionStateOf(card: AutomationCard): ActionState {
  const status = card.last_run?.status;
  if (!status) return "never-run";
  if (status === "failed" || status === "timeout") return "failing";
  if (status === "running") return "running";
  return "ready";
}

export default function RegisteredList({
  widgets,
  workflows,
  instances,
  filterId,
  runningId,
  onRun,
}: {
  widgets: AutomationWidget[];
  workflows: AutomationCard[];
  instances: AutomationInstance[];
  /** "all" or an instance id — see the filter chips beside the tab switcher. */
  filterId: string;
  runningId: string | null;
  onRun: (card: AutomationCard) => void;
}) {
  const [inspecting, setInspecting] = useState<AutomationWidget | null>(null);

  const byId = new Map(instances.map((instance) => [instance.id, instance]));
  // Origin is only information once there's a second instance to tell apart
  // — see AutomationWidgets.tsx's identical rule.
  const showOrigin = instances.length > 1;

  const filteredWidgets =
    filterId === "all" ? widgets : widgets.filter((w) => w.instance_id === filterId);
  const filteredWorkflows =
    filterId === "all" ? workflows : workflows.filter((w) => w.instance_id === filterId);

  const sortedWidgets = [...filteredWidgets].sort((a, b) =>
    (a.title || a.slug).localeCompare(b.title || b.slug),
  );
  const sortedWorkflows = [...filteredWorkflows].sort((a, b) =>
    (a.name ?? a.id).localeCompare(b.name ?? b.id),
  );

  const total = sortedWidgets.length + sortedWorkflows.length;

  return (
    <Panel label={`REGISTERED · ${total}`}>
      {total === 0 ? (
        <p className="text-label leading-relaxed text-ink-faint">
          {instances.length === 0
            ? "Connect an n8n instance to see what it registers here."
            : "Nothing registered yet. Push a widget from a workflow, or install one from the gallery."}
        </p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {sortedWidgets.map((widget) => {
            const instance = byId.get(widget.instance_id);
            const cadence = formatCadence(widget.expected_interval_seconds);
            const age = widget.last_seen_at ? formatRelativeTime(widget.last_seen_at) : null;
            const n8nUrl = workflowsUrl(instance?.base_url);
            const waiting = widget.state === "waiting";

            return (
              <li
                key={`${widget.instance_id}:${widget.slug}`}
                className="flex flex-wrap items-center gap-3 border border-line px-3 py-2.5"
              >
                <KindChip kind="display" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-label text-ink">{widget.title || widget.slug}</p>
                  <p className="mt-0.5 truncate font-mono text-meta text-ink-faint">
                    {widget.kind}
                    {age ? ` · pushed ${age}` : " · never pushed"}
                    {cadence ? ` · ${cadence}` : ""}
                  </p>
                </div>
                <OriginChip instanceId={widget.instance_id} name={showOrigin ? instance?.name : null} />
                <StateBadge state={widget.state} age={age} />
                {waiting ? (
                  <Button
                    variant="secondary"
                    onClick={() => n8nUrl && openExternalUrl(n8nUrl)}
                    disabled={!n8nUrl}
                    title={n8nUrl ? undefined : "this widget's instance is no longer registered"}
                  >
                    OPEN IN N8N
                  </Button>
                ) : (
                  <Button variant="secondary" onClick={() => setInspecting(widget)}>
                    INSPECT
                  </Button>
                )}
              </li>
            );
          })}

          {sortedWorkflows.map((card) => {
            const instance = byId.get(card.instance_id);
            const kindLabel =
              card.kind === "form"
                ? `${card.fields.length} field${card.fields.length === 1 ? "" : "s"}`
                : card.kind === "button"
                  ? "button"
                  : "no trigger";
            // Zero-input workflows run directly from here; a form with
            // fields needs its values filled in, which is the command
            // palette's job, not this management page's.
            const runnable = card.kind === "button" || (card.kind === "form" && card.fields.length === 0);
            const running = runningId === card.id;
            const connected = instance?.connected ?? false;
            const canRun = connected && runnable && !running;
            const title = !connected
              ? "n8n is not connected"
              : !runnable
                ? card.kind === "form"
                  ? "this form takes input — run it from the command palette"
                  : "this workflow has no runnable trigger"
                : undefined;
            const lastRun = card.last_run;

            return (
              <li
                key={card.id}
                className="flex flex-wrap items-center gap-3 border border-line px-3 py-2.5"
              >
                <KindChip kind="action" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-label text-ink">{card.name ?? card.id}</p>
                  <p className="mt-0.5 truncate font-mono text-meta text-ink-faint">
                    {kindLabel}
                    {lastRun && (
                      <>
                        {" · last run "}
                        {formatRelativeTime(lastRun.started_at)}{" "}
                        <span className={RUN_STATUS_STYLE[lastRun.status] ?? "text-ink-faint"}>
                          {lastRun.status}
                        </span>
                      </>
                    )}
                  </p>
                </div>
                <OriginChip instanceId={card.instance_id} name={showOrigin ? instance?.name : null} />
                {card.basic_auth && <AuthChip />}
                <ActionStateBadge state={actionStateOf(card)} />
                <Button variant="primary" onClick={() => onRun(card)} disabled={!canRun} title={title}>
                  {running ? "RUNNING…" : "RUN"}
                </Button>
              </li>
            );
          })}
        </ul>
      )}

      {inspecting && (
        <WidgetInspectDialog
          widget={inspecting}
          instanceName={showOrigin ? byId.get(inspecting.instance_id)?.name : null}
          onClose={() => setInspecting(null)}
        />
      )}
    </Panel>
  );
}
