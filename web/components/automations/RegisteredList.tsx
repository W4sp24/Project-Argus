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

/**
 * Inactive outranks every run-derived state, because it is the only one that
 * says the workflow *cannot* run: n8n's production form/webhook URL 404s while
 * a workflow is inactive, and a schedule trigger never fires at all. Reading
 * `last_run` alone (which is all this did before) rendered a freshly-installed
 * template as READY beside a live RUN button that could only ever fail.
 */
/** Identity for duplicate detection: same name, same instance. */
function nameKeyOf(card: AutomationCard): string {
  return `${card.instance_id}::${(card.name ?? card.id).trim().toLowerCase()}`;
}

/**
 * Names appearing more than once on a single instance. Installing a template
 * twice produces exactly this — n8n allows duplicate names and assigns each
 * copy its own id, so nothing else on the row distinguishes them. Scoped per
 * instance because the same automation legitimately existing on two different
 * n8n instances is not a duplicate.
 */
function duplicateNameKeys(cards: AutomationCard[]): Set<string> {
  const seen = new Set<string>();
  const dupes = new Set<string>();
  for (const card of cards) {
    const key = nameKeyOf(card);
    if (seen.has(key)) dupes.add(key);
    else seen.add(key);
  }
  return dupes;
}

function actionStateOf(card: AutomationCard): ActionState {
  if (!card.active) return "inactive";
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
  activatingId,
  onActivate,
  forgettingId,
  onForget,
}: {
  widgets: AutomationWidget[];
  workflows: AutomationCard[];
  instances: AutomationInstance[];
  /** "all" or an instance id — see the filter chips beside the tab switcher. */
  filterId: string;
  activatingId: string | null;
  onActivate: (card: AutomationCard) => void;
  forgettingId: string | null;
  /** `destroy` distinguishes DELETE (gone from n8n) from × (untag only). */
  onForget: (card: AutomationCard, destroy: boolean) => void;
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

  // Computed over the filtered set the user is actually looking at, so the
  // badge always agrees with the rows visible beside it.
  const duplicateNames = duplicateNameKeys(filteredWorkflows);

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
            const inactive = !card.active;
            const activating = activatingId === card.id;
            const forgetting = forgettingId === card.id;
            const duplicate = duplicateNames.has(nameKeyOf(card));

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
                {duplicate && (
                  <span
                    className="shrink-0 border border-warn px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.1em] text-warn"
                    title="Another workflow on this instance has the same name — most likely a template installed more than once."
                  >
                    DUPLICATE
                  </span>
                )}
                <ActionStateBadge state={actionStateOf(card)} />
                {inactive ? (
                  <Button
                    variant="secondary"
                    onClick={() => onActivate(card)}
                    disabled={!connected || activating}
                    title={
                      connected
                        ? "n8n will refuse this until the workflow's credential is granted there"
                        : "n8n is not connected"
                    }
                  >
                    {activating ? "ACTIVATING…" : "ACTIVATE"}
                  </Button>
                ) : (
                  <Button
                    variant="primary"
                    onClick={() => onRun(card)}
                    disabled={!canRun}
                    title={title}
                  >
                    {running ? "RUNNING…" : "RUN"}
                  </Button>
                )}
                <Button
                  variant="secondary"
                  onClick={() => onForget(card, false)}
                  disabled={!connected || forgetting}
                  title="Unregister — drops the argus tag; the workflow stays in n8n"
                >
                  {forgetting ? "…" : "UNREGISTER"}
                </Button>
                <Button
                  variant="danger"
                  onClick={() => onForget(card, true)}
                  disabled={!connected || forgetting}
                  title="Delete this workflow from n8n — cannot be undone"
                >
                  DELETE
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
