"use client";

import Panel from "@/components/Panel";
import { obsidianUri } from "@/lib/citations";
import Button from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/useConfirm";
import { mutateJSON, useActivity, useVault } from "@/lib/api";

const KIND_BADGE: Record<string, string> = {
  note: "text-[var(--ac)]",
  approval: "text-ok",
  exam: "text-danger",
};

function relative(when: string): string {
  const then = new Date(when.replace(" ", "T"));
  const minutes = Math.max(0, Math.round((Date.now() - then.getTime()) / 60000));
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)}h ago`;
  return `${Math.round(minutes / (60 * 24))}d ago`;
}

/** ACTIVITY.FEED (§4 General, right rail) — restyled, same data + delete flow. */
export default function ActivityFeed() {
  const { data: events, mutate } = useActivity();
  const { data: vault } = useVault();
  const { confirm, confirmDialog } = useConfirm();

  async function removeNote(path: string) {
    const answer = await confirm({
      label: "Delete note",
      message: `Delete ${path}?`,
      detail: "A git snapshot makes this undoable.",
      confirmLabel: "Delete",
    });
    if (answer === null) return;
    try {
      await mutateJSON(`/api/note?path=${encodeURIComponent(path)}`, undefined, "DELETE");
    } catch {
      // Feed refresh below surfaces the current truth either way.
    }
    mutate();
  }

  return (
    <Panel label="ACTIVITY.FEED">
      {!events && <p className="text-sm text-ink-faint">Loading…</p>}
      {events && events.length === 0 && <p className="text-sm text-ink-muted">All quiet.</p>}
      <ul className="divide-y divide-line">
        {(events ?? []).map((event, i) => (
          <li key={i} className="flex items-baseline gap-2 py-2 text-body">
            <span className={`shrink-0 font-mono text-micro uppercase ${KIND_BADGE[event.kind] ?? "text-ink-faint"}`}>
              {event.kind}
            </span>
            {event.path && vault ? (
              <a
                href={obsidianUri(vault.path, event.path)}
                className="min-w-0 flex-1 truncate text-ink-muted underline-offset-2 hover:text-ink hover:underline"
              >
                {event.title}
              </a>
            ) : (
              <span className="min-w-0 flex-1 truncate text-ink-muted">{event.title}</span>
            )}
            {event.kind === "note" && event.path?.startsWith("00-Inbox/") && (
              <Button
                variant="ghost"
                aria-label={`Delete ${event.path}`}
                onClick={() => removeNote(event.path!)}
                className="shrink-0 hover:text-danger"
              >
                ×
              </Button>
            )}
            <span className="shrink-0 font-mono text-meta text-ink-faint">{relative(event.when)}</span>
          </li>
        ))}
      </ul>
      {confirmDialog}
    </Panel>
  );
}
