"use client";

import { stateToneClass } from "../WidgetRenderer";

interface Entry {
  at?: unknown;
  text?: unknown;
  sub?: unknown;
  state?: unknown;
}

/** `at` is an ISO timestamp from a workflow, so it may be malformed or absent.
 * A bad timestamp shows as a dash rather than "Invalid Date" or a crash. */
function clockOf(at: unknown): string {
  if (typeof at !== "string" || !at) return "—";
  const parsed = new Date(at);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function Timeline({ payload }: { payload: unknown }) {
  const raw = (payload ?? {}) as { entries?: unknown };
  const entries = Array.isArray(raw.entries) ? (raw.entries as Entry[]) : null;

  if (entries === null) {
    return <p className="text-label text-ink-faint">This timeline&rsquo;s payload has no entries.</p>;
  }
  if (entries.length === 0) {
    return <p className="text-label text-ink-faint">Nothing scheduled.</p>;
  }

  return (
    <ol className="flex flex-col">
      {entries.slice(0, 50).map((entry, index) => (
        <li
          key={index}
          className="flex items-baseline gap-3 border-l border-line py-1.5 pl-3 first:pt-0 last:pb-0"
        >
          <span className="w-12 shrink-0 font-mono text-meta text-ink-faint">
            {clockOf(entry.at)}
          </span>
          <span className="min-w-0 flex-1">
            <span
              className={`block truncate text-body ${
                entry.state ? stateToneClass(entry.state) : "text-ink"
              }`}
            >
              {typeof entry.text === "string" && entry.text ? entry.text : "(untitled)"}
            </span>
            {typeof entry.sub === "string" && entry.sub && (
              <span className="block truncate text-meta text-ink-faint">{entry.sub}</span>
            )}
          </span>
        </li>
      ))}
    </ol>
  );
}
