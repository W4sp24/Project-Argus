"use client";

import { useMemo, useState } from "react";
import Panel from "@/components/Panel";
import PageHeader from "@/components/PageHeader";
import TemplateGallery from "@/components/automations/TemplateGallery";
import ConnectN8nDialog from "@/components/system/ConnectN8nDialog";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import { FIELD_CONTROL } from "@/components/ui/Field";
import SegmentedControl from "@/components/ui/SegmentedControl";
import { useConfirm } from "@/components/ui/useConfirm";
import {
  deleteAutomationWidget,
  deleteN8nInstance,
  refreshAutomations,
  runAutomation,
  useAutomationRuns,
  useAutomationWidgets,
  useAutomations,
  type AutomationCard,
  type AutomationWidget,
} from "@/lib/api";
import { formatRelativeTime } from "@/lib/relativeTime";

/**
 * AUTOMATIONS — management only: install an n8n instance, see what's
 * registered and whether it's healthy, fix what's broken. Deliberately not
 * where an automation gets *used* — a form/button action fires from the
 * command palette (a later chunk owns that surface), and a pushed display
 * lives on the dashboard. This page's RUN button exists for zero-input
 * workflows and quick health checks, not as a second way to fill out a form.
 */

type Tab = "GALLERY" | "ACTIVE" | "ACTIVITY";
const TABS: Tab[] = ["GALLERY", "ACTIVE", "ACTIVITY"];
const TAB_LABELS: Record<Tab, string> = { GALLERY: "GALLERY", ACTIVE: "ACTIVE", ACTIVITY: "ACTIVITY" };

const WIDGET_STATE_STYLE: Record<string, string> = {
  LIVE: "border-ok text-ok",
  STALE: "border-amber-400 text-amber-400",
  EMPTY: "border-ink-faint text-ink-faint",
  WAITING: "border-ink-faint text-ink-faint",
};

const RUN_STATUS_STYLE: Record<string, string> = {
  ok: "text-ok",
  running: "text-amber-400",
  failed: "text-danger",
  timeout: "text-danger",
};

function StateBadge({ state }: { state: string }) {
  const upper = state.toUpperCase();
  return (
    <span
      className={`shrink-0 border px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.1em] ${
        WIDGET_STATE_STYLE[upper] ?? WIDGET_STATE_STYLE.WAITING
      }`}
    >
      {upper}
    </span>
  );
}

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

export default function AutomationsPage() {
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();
  const { data, isLoading, mutate } = useAutomations();
  const { data: widgets, mutate: mutateWidgets } = useAutomationWidgets();
  const { data: runs } = useAutomationRuns(undefined, 200);

  const [tab, setTab] = useState<Tab>("ACTIVE");
  const [connecting, setConnecting] = useState(false);
  const [filterId, setFilterId] = useState<string>("all");
  const [runningId, setRunningId] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const instance = data?.instance ?? null;
  const workflows = data?.workflows ?? [];
  const connected = data?.connected ?? false;

  // Filter options come from the runs actually on record, not the current
  // workflow cache — a workflow untagged/deleted in n8n still has history
  // worth filtering to, even after it drops out of `workflows`.
  const workflowOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const entry of runs ?? []) {
      if (!seen.has(entry.workflow_id)) {
        seen.set(entry.workflow_id, entry.workflow_name ?? entry.workflow_id);
      }
    }
    return Array.from(seen.entries());
  }, [runs]);

  const filteredRuns =
    filterId === "all" ? (runs ?? []) : (runs ?? []).filter((entry) => entry.workflow_id === filterId);

  async function disconnect() {
    const answer = await confirm({
      label: "Disconnect n8n",
      message: `Forget the stored connection to ${instance?.name ?? "this n8n instance"}?`,
      detail: "Its API key is removed from the keyring, and cached workflow cards are cleared.",
      confirmLabel: "DISCONNECT",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await deleteN8nInstance();
      show("automations :: disconnected");
      void mutate();
    } catch (error) {
      show(error instanceof Error ? error.message : "could not disconnect", { tone: "error" });
    }
  }

  async function refresh() {
    if (refreshing) return;
    setRefreshing(true);
    try {
      const result = await refreshAutomations();
      show(
        `automations :: refreshed — ${result.count} workflow${result.count === 1 ? "" : "s"}${
          result.dropped ? `, ${result.dropped} dropped` : ""
        }`,
      );
      void mutate();
    } catch (error) {
      show(error instanceof Error ? error.message : "refresh failed", { tone: "error" });
    } finally {
      setRefreshing(false);
    }
  }

  async function runWorkflow(card: AutomationCard) {
    if (runningId) return;
    if (card.confirm) {
      const answer = await confirm({
        label: `Run ${card.name ?? card.id}`,
        message: `Run "${card.name ?? card.id}" now?`,
        detail: "Tagged argus:confirm — n8n treats this workflow as consequential.",
        confirmLabel: "RUN",
        tone: "primary",
      });
      if (answer === null) return;
    }
    setRunningId(card.id);
    try {
      const result = await runAutomation(card.id, {});
      const label = (card.name ?? card.id).toLowerCase();
      show(`automations :: ${result.status} — ${label}${result.message ? ` (${result.message})` : ""}`);
      void mutate();
    } catch (error) {
      show(error instanceof Error ? error.message : "run failed", { tone: "error" });
    } finally {
      setRunningId(null);
    }
  }

  async function removeWidget(widget: AutomationWidget) {
    const answer = await confirm({
      label: `Remove ${widget.title ?? widget.slug}`,
      message: `Remove the "${widget.title ?? widget.slug}" display from the dashboard?`,
      detail: "The next matching push recreates it.",
      confirmLabel: "REMOVE",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await deleteAutomationWidget(widget.slug);
      show(`automations :: removed — ${(widget.title ?? widget.slug).toLowerCase()}`);
      void mutateWidgets();
    } catch (error) {
      show(error instanceof Error ? error.message : "could not remove that display", { tone: "error" });
    }
  }

  return (
    <>
      <PageHeader
        label="AUTOMATIONS"
        title="Automations"
        subtitle="Install, inspect health, and fix n8n automations. To use one, open the command palette or the dashboard."
      />

      <Panel
        label="N8N.INSTANCE"
        headerRight={
          instance ? (
            <div className="flex items-center gap-2">
              <Button variant="quiet" onClick={() => void refresh()} disabled={refreshing}>
                {refreshing ? "REFRESHING…" : "REFRESH"}
              </Button>
              <Button variant="quiet" onClick={() => void disconnect()}>
                DISCONNECT
              </Button>
            </div>
          ) : (
            <Button variant="primary" onClick={() => setConnecting(true)}>
              + CONNECT N8N
            </Button>
          )
        }
      >
        {isLoading && !data ? (
          <p className="text-label text-ink-faint">loading…</p>
        ) : instance ? (
          <div className="flex flex-wrap items-center gap-3">
            <span
              className={`shrink-0 border px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.1em] ${
                connected ? "border-ok text-ok" : "border-danger text-danger"
              }`}
            >
              {connected ? "CONNECTED" : "DISCONNECTED"}
            </span>
            <p className="min-w-0 flex-1 truncate text-label text-ink">
              {instance.name} <span className="text-ink-faint">· {instance.base_url}</span>
            </p>
          </div>
        ) : (
          <p className="text-label leading-relaxed text-ink-faint">
            No n8n instance registered. Connecting one discovers every workflow tagged{" "}
            <code className="font-mono text-ink-muted">argus</code>.
          </p>
        )}
        {instance && !connected && data?.detail && (
          <p
            role="alert"
            className="mt-3 border border-danger px-3 py-2 text-label leading-relaxed text-danger"
          >
            {data.detail}
          </p>
        )}
      </Panel>

      <div className="mt-4">
        <SegmentedControl options={TABS} value={tab} onChange={setTab} labels={TAB_LABELS} />
      </div>

      <div className="mt-4">
        {tab === "GALLERY" && <TemplateGallery onInstalled={() => void mutate()} />}

        {tab === "ACTIVE" && (
          <div className="flex flex-col gap-4">
            <Panel label="ACTIONS">
              {workflows.length === 0 ? (
                <p className="text-label text-ink-faint">
                  {instance
                    ? "No workflows tagged argus were found."
                    : "Connect an n8n instance to see its workflows here."}
                </p>
              ) : (
                <ul className="flex flex-col gap-2.5">
                  {workflows.map((card) => (
                    <ActionRow
                      key={card.id}
                      card={card}
                      connected={connected}
                      running={runningId === card.id}
                      onRun={() => void runWorkflow(card)}
                    />
                  ))}
                </ul>
              )}
            </Panel>

            <Panel label="DISPLAYS">
              {!widgets || widgets.length === 0 ? (
                <p className="text-label text-ink-faint">No dashboard displays have been pushed yet.</p>
              ) : (
                <ul className="flex flex-col gap-2.5">
                  {widgets.map((widget) => (
                    <DisplayRow key={widget.slug} widget={widget} onRemove={() => void removeWidget(widget)} />
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        )}

        {tab === "ACTIVITY" && (
          <Panel
            label="ACTIVITY"
            headerRight={
              <select
                value={filterId}
                onChange={(event) => setFilterId(event.target.value)}
                aria-label="Filter activity by automation"
                className={`${FIELD_CONTROL} w-auto font-mono text-meta`}
              >
                <option value="all">all automations</option>
                {workflowOptions.map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </select>
            }
          >
            {!runs ? (
              <p className="text-label text-ink-faint">loading…</p>
            ) : filteredRuns.length === 0 ? (
              <p className="text-label text-ink-faint">No runs recorded yet.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {filteredRuns.map((entry) => (
                  <li key={entry.id} className="flex items-center gap-3 border border-line px-3 py-2">
                    <span className="w-16 shrink-0 font-mono text-meta text-ink-faint">
                      {formatRelativeTime(entry.started_at)}
                    </span>
                    <span
                      className={`w-16 shrink-0 font-mono text-micro uppercase tracking-[0.1em] ${
                        RUN_STATUS_STYLE[entry.status] ?? "text-ink-faint"
                      }`}
                    >
                      {entry.status}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-label text-ink">
                      {entry.workflow_name ?? entry.workflow_id}
                    </span>
                    {entry.message && (
                      <span className="min-w-0 max-w-[16rem] truncate text-label text-ink-faint">
                        {entry.message}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        )}
      </div>

      {connecting && (
        <ConnectN8nDialog onClose={() => setConnecting(false)} onConnected={() => void mutate()} />
      )}
      {confirmDialog}
    </>
  );
}

function ActionRow({
  card,
  connected,
  running,
  onRun,
}: {
  card: AutomationCard;
  connected: boolean;
  running: boolean;
  onRun: () => void;
}) {
  const kindLabel =
    card.kind === "form"
      ? `${card.fields.length} field${card.fields.length === 1 ? "" : "s"}`
      : card.kind === "button"
        ? "button"
        : "no trigger";
  // Zero-input workflows run directly from here; a form with fields needs
  // its values filled in, which is the command palette's job, not this
  // management page's.
  const runnable = card.kind === "button" || (card.kind === "form" && card.fields.length === 0);
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
    <li className="flex items-center gap-3 border border-line px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="truncate text-label text-ink">{card.name ?? card.id}</p>
        <p className="mt-0.5 truncate font-mono text-meta text-ink-faint">
          action · {kindLabel}
          {lastRun && (
            <>
              {" · last run "}
              {formatRelativeTime(lastRun.started_at)}{" "}
              <span className={RUN_STATUS_STYLE[lastRun.status] ?? "text-ink-faint"}>{lastRun.status}</span>
            </>
          )}
        </p>
      </div>
      <Button variant="primary" onClick={onRun} disabled={!canRun} title={title}>
        {running ? "RUNNING…" : "RUN"}
      </Button>
    </li>
  );
}

function DisplayRow({ widget, onRemove }: { widget: AutomationWidget; onRemove: () => void }) {
  const cadence = formatCadence(widget.expected_interval_seconds);
  return (
    <li className="flex items-center gap-3 border border-line px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="truncate text-label text-ink">{widget.title ?? widget.slug}</p>
        <p className="mt-0.5 truncate font-mono text-meta text-ink-faint">
          display · {widget.kind} · pushed {formatRelativeTime(widget.last_seen_at)}
          {cadence ? ` · ${cadence}` : ""}
        </p>
      </div>
      <StateBadge state={widget.state} />
      <Button variant="quiet" onClick={onRemove}>
        REMOVE
      </Button>
    </li>
  );
}
