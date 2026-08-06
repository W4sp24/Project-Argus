"use client";

import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import type { AutomationWidget } from "@/lib/api";
import { formatRelativeTime } from "@/lib/relativeTime";
import { AutoChip, OriginChip, StateBadge } from "./chips";
import WidgetRenderer from "./WidgetRenderer";

/**
 * The ACTIVE tab's INSPECT action — a read-only look at one live/stale/empty
 * widget's current payload, without leaving the management page for the
 * dashboard. Reuses the same `WidgetRenderer` the dashboard grid renders
 * with, so what you see here is exactly what you'd see there.
 */
export default function WidgetInspectDialog({
  widget,
  instanceName,
  onClose,
}: {
  widget: AutomationWidget;
  /** Omit when only one instance is registered — see OriginChip. */
  instanceName?: string | null;
  onClose: () => void;
}) {
  const age = widget.last_seen_at ? formatRelativeTime(widget.last_seen_at) : null;

  return (
    <Dialog
      label={`Inspect ${widget.title || widget.slug}`}
      onClose={onClose}
      align="center"
      className="w-[36rem] max-w-[calc(100vw-2rem)]"
    >
      <div className="p-5">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <p className="eyebrow min-w-0 flex-1 truncate">{`▍${widget.title || widget.slug}`}</p>
          <AutoChip />
          <OriginChip instanceId={widget.instance_id} name={instanceName} />
          <StateBadge state={widget.state} age={age} />
        </div>

        <WidgetRenderer kind={widget.kind} payload={widget.payload} />

        <p className="mt-4 font-mono text-meta text-ink-faint">
          {age ? `updated ${age}` : "never updated"}
        </p>

        <div className="mt-5 flex justify-end border-t border-line pt-4">
          <Button size="md" onClick={onClose}>
            CLOSE
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
