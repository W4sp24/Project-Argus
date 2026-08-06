"use client";

import { useEffect, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/useConfirm";
import {
  deleteAutomationInstance,
  refreshAutomationInstance,
  type AutomationCard,
  type AutomationInstance,
  type AutomationWidget,
} from "@/lib/api";
import { originColor } from "./chips";

/**
 * The instance registry — one card per registered n8n instance, replacing
 * the old single N8N.INSTANCE panel now that the backend supports several
 * (F5). A dashed `+ ADD INSTANCE` card is always the last cell.
 */

/** Same markup/weight as KindChip, but colored per-instance via
 * `originColor` rather than the DISPLAY/ACTION semantic colors — this is an
 * identity marker, not a kind marker. */
function InstanceKindTag({ id, kind }: { id: string; kind: string }) {
  const color = originColor(id);
  return (
    <span
      className="inline-flex w-20 shrink-0 items-center justify-center border px-1 py-px font-mono text-micro uppercase tracking-[0.14em]"
      style={{ borderColor: color, color }}
    >
      {kind}
    </span>
  );
}

function ManageMenu({
  busy,
  onRefresh,
  onDisconnect,
}: {
  busy: boolean;
  onRefresh: () => void;
  onDisconnect: () => void;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocDown(event: MouseEvent) {
      if (!wrap.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={wrap} className="relative">
      <Button
        variant="quiet"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={busy}
      >
        {busy ? "WORKING…" : "MANAGE"}
      </Button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-30 mt-1 w-40 border border-line bg-panel"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onRefresh();
            }}
            className="block w-full border-b border-line px-3 py-2 text-left font-mono text-meta uppercase tracking-[0.12em] text-ink-muted hover:text-ink-bright"
          >
            REFRESH
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onDisconnect();
            }}
            className="block w-full px-3 py-2 text-left font-mono text-meta uppercase tracking-[0.12em] text-danger hover:bg-[rgba(251,113,133,0.08)]"
          >
            DISCONNECT
          </button>
        </div>
      )}
    </div>
  );
}

export default function InstanceGrid({
  instances,
  workflows,
  widgets,
  onChanged,
  onAdd,
}: {
  instances: AutomationInstance[];
  workflows: AutomationCard[];
  widgets: AutomationWidget[];
  /** Revalidate every automations-related SWR resource — a refresh or
   * disconnect can move instance/workflow/widget state all at once. */
  onChanged: () => void;
  onAdd: () => void;
}) {
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();
  const [busyId, setBusyId] = useState<string | null>(null);

  async function refresh(instance: AutomationInstance) {
    setBusyId(instance.id);
    try {
      const result = await refreshAutomationInstance(instance.id);
      show(
        `automations :: ${instance.name} refreshed — ${result.count} workflow${
          result.count === 1 ? "" : "s"
        }${result.dropped ? `, ${result.dropped} dropped` : ""}`,
      );
      onChanged();
    } catch (error) {
      show(error instanceof Error ? error.message : "could not reach this instance", {
        tone: "error",
      });
    } finally {
      setBusyId(null);
    }
  }

  async function disconnect(instance: AutomationInstance) {
    const actionCount = workflows.filter((w) => w.instance_id === instance.id).length;
    const widgetCount = widgets.filter((w) => w.instance_id === instance.id).length;
    const stops: string[] = [];
    if (actionCount > 0) stops.push(`${actionCount} action${actionCount === 1 ? "" : "s"}`);
    if (widgetCount > 0) stops.push(`${widgetCount} display${widgetCount === 1 ? "" : "s"}`);

    const answer = await confirm({
      label: `Disconnect ${instance.name}`,
      message: `Forget the stored connection to ${instance.name}?`,
      detail:
        stops.length > 0
          ? `${stops.join(" and ")} fed from it stop working, and its API key is removed from the keyring.`
          : "Its API key is removed from the keyring.",
      confirmLabel: "DISCONNECT",
      tone: "danger",
    });
    if (answer === null) return;

    setBusyId(instance.id);
    try {
      await deleteAutomationInstance(instance.id);
      show(`automations :: disconnected — ${instance.name.toLowerCase()}`);
      onChanged();
    } catch (error) {
      show(error instanceof Error ? error.message : "could not disconnect", { tone: "error" });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {instances.map((instance) => {
          const actionCount = workflows.filter((w) => w.instance_id === instance.id).length;
          const widgetCount = widgets.filter((w) => w.instance_id === instance.id).length;
          const busy = busyId === instance.id;

          return (
            <li
              key={instance.id}
              className="flex flex-col gap-2 border border-line bg-panel p-4 transition-colors hover:border-lineHi"
            >
              <div className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className={`h-2 w-2 shrink-0 rounded-full ${
                    instance.connected ? "bg-ok" : "bg-danger"
                  }`}
                />
                <p className="min-w-0 flex-1 truncate text-label text-ink-bright">
                  {instance.name}
                </p>
                <InstanceKindTag id={instance.id} kind={instance.kind} />
              </div>

              <p className="truncate font-mono text-meta text-ink-faint">{instance.base_url}</p>

              <p className="text-meta text-ink-faint">
                {actionCount} action{actionCount === 1 ? "" : "s"} · {widgetCount} display
                {widgetCount === 1 ? "" : "s"}
              </p>

              <div className="mt-auto flex items-center justify-between gap-2 pt-2">
                <span
                  className={`border px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.1em] ${
                    instance.connected ? "border-ok text-ok" : "border-danger text-danger"
                  }`}
                >
                  {instance.connected ? "WIRED" : "UNREACHABLE"}
                </span>

                {instance.connected ? (
                  <ManageMenu
                    busy={busy}
                    onRefresh={() => void refresh(instance)}
                    onDisconnect={() => void disconnect(instance)}
                  />
                ) : (
                  <Button variant="quiet" onClick={() => void refresh(instance)} disabled={busy}>
                    {busy ? "RECONNECTING…" : "RECONNECT"}
                  </Button>
                )}
              </div>
            </li>
          );
        })}

        <li>
          <button
            type="button"
            onClick={onAdd}
            className="flex h-full min-h-[9rem] w-full flex-col items-center justify-center gap-1 border border-dashed border-line font-mono text-ink-faint transition-colors hover:border-lineHi hover:text-ink"
          >
            <span aria-hidden="true" className="text-title leading-none">
              +
            </span>
            <span className="text-micro uppercase tracking-[0.14em]">ADD INSTANCE</span>
          </button>
        </li>
      </ul>

      {confirmDialog}
    </>
  );
}
