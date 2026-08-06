"use client";

import { useEffect, useRef, useState } from "react";

import Panel from "@/components/Panel";
import type { AutomationWidget } from "@/lib/api";
import { openExternalUrl } from "@/lib/quickLinks";
import { formatRelativeTime } from "@/lib/relativeTime";
import { AutoChip, OriginChip, StateBadge } from "./chips";
import WidgetRenderer from "./WidgetRenderer";

/**
 * The chrome around a pushed widget, and the four states it can be in.
 *
 * Grafana had to make this distinction explicit and so does Argus: a panel
 * showing nothing because the source legitimately returned nothing is a
 * different thing from a panel showing nothing because nothing ever arrived.
 * Collapsing them into one blank card was a real bug in the first draft of
 * this design. **Three of the four states are not failures.**
 *
 * - LIVE — fresh within its declared cadence.
 * - STALE — past 2.5x it. The last good data is still shown, because old data
 *   labelled as old beats no data at all.
 * - EMPTY — pushed fine, genuinely zero items. Must read as reassurance.
 * - WAITING — installed, nothing has arrived yet.
 *
 * `state` is computed by the backend from `last_seen_at` and the declared
 * interval; it is never recomputed here, so one clock decides.
 */

function ageOf(iso: string | null | undefined): string | null {
  if (!iso) return null;
  if (Number.isNaN(new Date(iso).getTime())) return null;
  return formatRelativeTime(iso);
}

function cadenceOf(seconds: number | null | undefined): string | null {
  if (!seconds || seconds <= 0) return null;
  if (seconds < 60) return `every ${seconds}s`;
  if (seconds < 3600) return `every ${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `every ${Math.round(seconds / 3600)}h`;
  return `every ${Math.round(seconds / 86400)}d`;
}

/** n8n's workflow list. A widget slug does not identify a workflow — the
 * payload names the widget, not the thing that pushed it — so this is
 * deliberately the instance's list rather than a deep link, and the copy
 * says so. Guessing a workflow id would be worse than being honest. */
function workflowsUrl(baseUrl: string | null | undefined): string | null {
  if (!baseUrl) return null;
  return `${baseUrl.replace(/\/+$/, "")}/home/workflows`;
}

function OverflowMenu({
  items,
}: {
  items: { label: string; run: () => void }[];
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

  if (items.length === 0) return null;

  return (
    <div ref={wrap} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Widget actions"
        onClick={() => setOpen((v) => !v)}
        className="px-1 font-mono text-micro tracking-[0.14em] text-ink-faint hover:text-ink"
      >
        ⋯
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-30 mt-1 w-44 border border-line bg-panel"
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                item.run();
              }}
              className="block w-full border-b border-line px-3 py-2 text-left font-mono text-meta uppercase tracking-[0.12em] text-ink-muted last:border-b-0 hover:text-ink-bright"
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function WidgetShell({
  widget,
  instanceName,
  instanceBaseUrl,
  actions,
  menuItems = [],
  controlled = false,
}: {
  widget: AutomationWidget;
  /** Origin label. Omit when only one instance is registered — a chip that
   * always says the same thing is noise, so OriginChip renders null. */
  instanceName?: string | null;
  /** Base URL of the instance that pushed this, for the n8n links. */
  instanceBaseUrl?: string | null;
  actions?: React.ReactNode;
  /** Extra entries for the ⋯ menu, appended after OPEN IN N8N. */
  menuItems?: { label: string; run: () => void }[];
  /** The user has taken control of this widget's grid layout (dragged or
   * resized it). Renders a `▣` marker on the freshness line rather than
   * duplicating that line per caller — see AutomationWidgets.tsx. */
  controlled?: boolean;
}) {
  const age = ageOf(widget.last_seen_at);
  const cadence = cadenceOf(widget.expected_interval_seconds);
  const stale = widget.state === "stale";
  const waiting = widget.state === "waiting";
  const empty = widget.state === "empty";
  const n8nUrl = workflowsUrl(instanceBaseUrl);

  const menu = [
    ...(n8nUrl ? [{ label: "OPEN IN N8N", run: () => openExternalUrl(n8nUrl) }] : []),
    ...menuItems,
  ];

  const controlMarker = controlled ? (
    <>
      <span aria-hidden="true">{"▣ "}</span>
      <span className="sr-only">Under your control — </span>
    </>
  ) : null;

  return (
    <Panel
      label={widget.title || widget.slug}
      // STALE turns the *label* amber and dims only the body below. Dimming
      // the label along with the data would make the warning itself harder to
      // read, which is backwards: the whole point of this state is that the
      // stale data stays visible and clearly marked as stale.
      //
      // `h-full` so the panel fills its grid cell in AutomationWidgets' grid
      // (a widget spanning 2 rows should not leave its own background short
      // of the cell it was given). Harmless outside a grid — WidgetShell has
      // exactly one caller.
      className={`h-full${stale ? " [&_.eyebrow]:text-warn" : ""}`}
      headerRight={
        <div className="flex items-center gap-2">
          {/* Provenance. Native panels get no AUTO tag and no freshness line —
              which matters most after the migration, when the panels you rely
              on most are the ones fed by a second service staying alive. */}
          <AutoChip />
          <OriginChip instanceId={widget.instance_id} name={instanceName} />
          <StateBadge state={widget.state} age={age} />
          {actions}
          <OverflowMenu items={menu} />
        </div>
      }
    >
      {waiting ? (
        <div className="flex flex-col gap-2">
          <p className="text-label text-ink-muted">No data received yet.</p>
          {/* WAITING links into n8n because the two overwhelmingly likely
              causes are a workflow that was never activated and a credential
              that was never granted — both fixed there, not here. The state
              that names the problem should open the place it gets solved. */}
          {n8nUrl && (
            <button
              type="button"
              onClick={() => openExternalUrl(n8nUrl)}
              className="w-fit border border-line px-2.5 py-1 font-mono text-meta uppercase tracking-[0.12em] text-[var(--ac)] hover:border-lineHi"
            >
              OPEN IN N8N →
            </button>
          )}
          <p className="font-mono text-meta text-ink-faint">
            {controlMarker}
            {age ? `installed ${age}` : "installed"} · workflow may not be active
          </p>
        </div>
      ) : (
        <>
          <div className={stale ? "opacity-60" : undefined}>
            <WidgetRenderer kind={widget.kind} payload={widget.payload} />
          </div>
          <p
            className={`mt-3 font-mono text-meta ${stale ? "text-warn" : "text-ink-faint"}`}
          >
            {controlMarker}
            {stale
              ? [age ? `last push ${age}` : "last push unknown", cadence]
                  .filter(Boolean)
                  .join(" · ")
              : [
                  age ? `updated ${age}` : "updated just now",
                  cadence,
                  // EMPTY must read as reassurance, not as a fault. The
                  // renderer already says "Nothing here." for its own kind;
                  // this confirms the source answered and answered with
                  // nothing, which is what separates it from WAITING.
                  empty ? "reported empty" : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
          </p>
        </>
      )}
    </Panel>
  );
}
