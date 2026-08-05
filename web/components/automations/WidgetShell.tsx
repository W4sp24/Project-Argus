"use client";

import Panel from "@/components/Panel";
import type { AutomationWidget } from "@/lib/api";
import { formatRelativeTime } from "@/lib/relativeTime";
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
 * - STALE — past 2.5x it. Dimmed and amber, but the last good data is still
 *   shown, because old data labelled as old beats no data at all.
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

export default function WidgetShell({
  widget,
  openInN8n,
  actions,
}: {
  widget: AutomationWidget;
  /** Where WAITING sends the user. See the comment on that branch below. */
  openInN8n?: string;
  actions?: React.ReactNode;
}) {
  const age = ageOf(widget.last_seen_at);
  const cadence = cadenceOf(widget.expected_interval_seconds);
  const stale = widget.state === "stale";
  const waiting = widget.state === "waiting";

  const badge = stale ? (
    <span className="border border-warn px-1 py-px font-mono text-micro uppercase tracking-[0.14em] text-warn">
      {age ? `STALE ${age}` : "STALE"}
    </span>
  ) : waiting ? (
    <span className="border border-line px-1 py-px font-mono text-micro uppercase tracking-[0.14em] text-ink-faint">
      WAITING
    </span>
  ) : null;

  return (
    <Panel
      label={widget.title || widget.slug}
      className={stale ? "opacity-75" : undefined}
      headerRight={
        <div className="flex items-center gap-2">
          {/* Provenance. Native panels get no AUTO tag and no freshness line —
              which matters most after the migration, when the panels you rely
              on most are the ones fed by a second service staying alive. */}
          <span className="border border-line px-1 py-px font-mono text-micro uppercase tracking-[0.14em] text-ink-faint">
            AUTO
          </span>
          {badge}
          {actions}
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
          {openInN8n && (
            <a
              href={openInN8n}
              target="_blank"
              rel="noreferrer"
              className="w-fit border border-line px-2.5 py-1 font-mono text-meta uppercase tracking-[0.12em] text-[var(--ac)] hover:border-lineHi"
            >
              OPEN IN N8N →
            </a>
          )}
          <p className="font-mono text-meta text-ink-faint">
            {age ? `installed ${age}` : "installed"} · workflow may not be active
          </p>
        </div>
      ) : (
        <>
          <WidgetRenderer kind={widget.kind} payload={widget.payload} />
          <p className="mt-3 font-mono text-meta text-ink-faint">
            {stale
              ? [age ? `last push ${age}` : "last push unknown", cadence]
                  .filter(Boolean)
                  .join(" · ")
              : [age ? `updated ${age}` : "updated just now", cadence]
                  .filter(Boolean)
                  .join(" · ")}
          </p>
        </>
      )}
    </Panel>
  );
}
