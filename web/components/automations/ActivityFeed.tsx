"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useAutomationEvents } from "@/lib/api";
import { formatRelativeTime } from "@/lib/relativeTime";

/**
 * The ACTIVITY tab: a real feed of `automation_events` — runs, pushes,
 * failures, installs, and captures — not only run history. Filter chips
 * replace the old per-workflow `<select>`.
 */

const FILTERS = ["all", "run", "push", "fail"] as const;
type EventFilter = (typeof FILTERS)[number];

/** FAIL danger, RUN blue, INSTALL accent, PUSH/CAPTURE ok — per the design. */
const TAG_STYLE: Record<string, string> = {
  FAIL: "border-danger text-danger",
  RUN: "border-[#60a5fa] text-[#60a5fa]",
  INSTALL: "border-[var(--ac)] text-[var(--ac)]",
  PUSH: "border-ok text-ok",
  CAPTURE: "border-ok text-ok",
};

export default function ActivityFeed({ instanceId }: { instanceId?: string }) {
  const [filter, setFilter] = useState<EventFilter>("all");
  const { data: events } = useAutomationEvents(
    filter === "all" ? undefined : filter,
    instanceId,
    200,
  );

  return (
    <Panel label={`ACTIVITY${events ? ` · ${events.length}` : ""}`}>
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        {FILTERS.map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => setFilter(option)}
            aria-pressed={filter === option}
            className={`border px-2 py-1 font-mono text-micro uppercase tracking-[0.12em] transition-colors ${
              filter === option
                ? "border-lineHi bg-[var(--ac-bg)] text-[var(--ac)]"
                : "border-line text-ink-faint hover:text-ink-muted"
            }`}
          >
            {option}
          </button>
        ))}
      </div>

      {!events ? (
        <p className="text-label text-ink-faint">loading…</p>
      ) : events.length === 0 ? (
        <p className="text-label leading-relaxed text-ink-faint">
          {filter === "all"
            ? "No activity recorded yet. Runs, pushes, and installs show up here as they happen."
            : `No ${filter} events recorded yet.`}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {events.map((event, index) => (
            <li
              key={`${event.instance_id}:${event.ts}:${index}`}
              className="flex flex-wrap items-center gap-3 border border-line px-3 py-2"
            >
              <span className="w-16 shrink-0 font-mono text-meta text-ink-faint">
                {formatRelativeTime(event.ts)}
              </span>
              <span
                className={`w-20 shrink-0 border px-1.5 py-0.5 text-center font-mono text-micro uppercase tracking-[0.1em] ${
                  TAG_STYLE[event.tag] ?? "border-line text-ink-faint"
                }`}
              >
                {event.tag}
              </span>
              {event.subject && (
                <span className="min-w-0 max-w-[10rem] truncate font-mono text-meta text-ink-muted">
                  {event.subject}
                </span>
              )}
              <span className="min-w-0 flex-1 truncate text-label text-ink">{event.text}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
