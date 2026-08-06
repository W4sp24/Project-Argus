"use client";

import { useEffect, useMemo, useState } from "react";
import ModeHeader from "@/components/ModeHeader";
import ActivityFeed from "@/components/automations/ActivityFeed";
import { originColor } from "@/components/automations/chips";
import InstanceGrid from "@/components/automations/InstanceGrid";
import RegisteredList from "@/components/automations/RegisteredList";
import TemplateGallery from "@/components/automations/TemplateGallery";
import ConnectN8nDialog from "@/components/system/ConnectN8nDialog";
import { useToast } from "@/components/Toast";
import SegmentedControl from "@/components/ui/SegmentedControl";
import { useConfirm } from "@/components/ui/useConfirm";
import {
  runAutomation,
  useAutomationInstances,
  useAutomationWidgets,
  useAutomations,
  type AutomationCard,
} from "@/lib/api";

/**
 * AUTOMATIONS — management only: register n8n instances, see what's
 * registered across all of them and whether each is healthy, fix what's
 * broken. Deliberately not where an automation gets *used* — a form/button
 * action fires from the command palette, and a pushed display lives on the
 * dashboard. This page's RUN button exists for zero-input workflows and
 * quick health checks, not as a second way to fill out a form.
 *
 * F5: rebuilt around the multi-instance backend (an instance grid instead of
 * one N8N.INSTANCE panel, an instance filter, a unified REGISTERED list, and
 * a real activity feed instead of run history alone).
 */

type Tab = "ACTIVE" | "GALLERY" | "ACTIVITY";
const TABS: Tab[] = ["ACTIVE", "GALLERY", "ACTIVITY"];
const TAB_LABELS: Record<Tab, string> = { ACTIVE: "ACTIVE", GALLERY: "GALLERY", ACTIVITY: "ACTIVITY" };

export default function AutomationsPage() {
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();

  const { data: instancesData, mutate: mutateInstances } = useAutomationInstances();
  const { data: automations, mutate: mutateAutomations } = useAutomations();
  const { data: widgetsData, mutate: mutateWidgets } = useAutomationWidgets();

  const [tab, setTab] = useState<Tab>("ACTIVE");
  const [filterId, setFilterId] = useState<string>("all");
  const [connecting, setConnecting] = useState(false);
  const [runningId, setRunningId] = useState<string | null>(null);

  // Memoized so the "reset a stale filter" effect below doesn't see a new
  // array identity (and re-fire) on every render — only when the data itself
  // changes.
  const instances = useMemo(() => instancesData ?? [], [instancesData]);
  const workflows = automations?.workflows ?? [];
  const widgets = widgetsData ?? [];

  // A disconnected/removed instance can leave the filter pointed at an id
  // that no longer exists — fall back to ALL rather than silently filtering
  // everything out.
  useEffect(() => {
    if (filterId !== "all" && !instances.some((instance) => instance.id === filterId)) {
      setFilterId("all");
    }
  }, [filterId, instances]);

  function refreshAll() {
    void mutateInstances();
    void mutateAutomations();
    void mutateWidgets();
  }

  // Every number in the sysline traces to a real endpoint:
  //   N instances   — GET /automations/instances
  //   M registered  — widgets.length + workflows.length (both from their own endpoints)
  //   X stale/Y waiting — GET /automations/widgets, grouped by computed `state`
  //   Z failing     — GET /automations (workflows[]), by `last_run.status`
  const instanceCount = instancesData?.length;
  const registeredCount =
    widgetsData && automations ? widgetsData.length + automations.workflows.length : undefined;
  const staleCount = widgetsData?.filter((widget) => widget.state === "stale").length;
  const waitingCount = widgetsData?.filter((widget) => widget.state === "waiting").length;
  const failingCount = automations?.workflows.filter(
    (card) =>
      card.last_run && (card.last_run.status === "failed" || card.last_run.status === "timeout"),
  ).length;

  const sysline = useMemo(() => {
    const segments: string[] = [];
    if (instanceCount !== undefined) {
      segments.push(`${instanceCount} instance${instanceCount === 1 ? "" : "s"}`);
    }
    if (registeredCount !== undefined) segments.push(`${registeredCount} registered`);
    if (staleCount !== undefined) segments.push(`${staleCount} stale`);
    if (waitingCount !== undefined) segments.push(`${waitingCount} waiting`);
    if (failingCount !== undefined) segments.push(`${failingCount} failing`);
    return segments.length > 0 ? segments.join(" · ") : "loading…";
  }, [instanceCount, registeredCount, staleCount, waitingCount, failingCount]);

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
      refreshAll();
    } catch (error) {
      show(error instanceof Error ? error.message : "run failed", { tone: "error" });
    } finally {
      setRunningId(null);
    }
  }

  const showInstanceFilter = instances.length > 1;

  return (
    <>
      <ModeHeader mode="auto" greeting="Automations online." status={sysline} />

      {instances.length === 0 && (
        <p className="mb-3 text-label leading-relaxed text-ink-muted">
          No n8n instance registered. Connecting one discovers every workflow tagged{" "}
          <code className="font-mono text-ink-muted">argus</code>.
        </p>
      )}

      <InstanceGrid
        instances={instances}
        workflows={workflows}
        widgets={widgets}
        onChanged={refreshAll}
        onAdd={() => setConnecting(true)}
      />

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <SegmentedControl options={TABS} value={tab} onChange={setTab} labels={TAB_LABELS} />

        {showInstanceFilter && (tab === "ACTIVE" || tab === "ACTIVITY") && (
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => setFilterId("all")}
              aria-pressed={filterId === "all"}
              className={`border px-2 py-1 font-mono text-micro uppercase tracking-[0.12em] transition-colors ${
                filterId === "all"
                  ? "border-lineHi bg-[var(--ac-bg)] text-[var(--ac)]"
                  : "border-line text-ink-faint hover:text-ink-muted"
              }`}
            >
              ALL
            </button>
            {instances.map((instance) => {
              const active = filterId === instance.id;
              const color = originColor(instance.id);
              return (
                <button
                  key={instance.id}
                  type="button"
                  onClick={() => setFilterId(instance.id)}
                  aria-pressed={active}
                  style={active ? { borderColor: color, color } : undefined}
                  className={`border px-2 py-1 font-mono text-micro uppercase tracking-[0.12em] transition-colors ${
                    active ? "" : "border-line text-ink-faint hover:text-ink-muted"
                  }`}
                >
                  {instance.name}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div className="mt-4">
        {tab === "ACTIVE" && (
          <RegisteredList
            widgets={widgets}
            workflows={workflows}
            instances={instances}
            filterId={filterId}
            runningId={runningId}
            onRun={(card) => void runWorkflow(card)}
          />
        )}

        {tab === "GALLERY" && <TemplateGallery onInstalled={refreshAll} />}

        {tab === "ACTIVITY" && <ActivityFeed instanceId={filterId === "all" ? undefined : filterId} />}
      </div>

      {connecting && (
        <ConnectN8nDialog onClose={() => setConnecting(false)} onConnected={refreshAll} />
      )}
      {confirmDialog}
    </>
  );
}
