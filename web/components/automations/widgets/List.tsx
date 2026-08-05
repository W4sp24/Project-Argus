"use client";

import { openExternalUrl } from "@/lib/quickLinks";
import { stateToneClass } from "../WidgetRenderer";

interface ListRow {
  text: string;
  sub?: string;
  href?: string;
  state?: string;
}

// A workflow gone wrong could push thousands of rows; this is a dashboard
// tile, not a table view, so rendering is capped well below where the DOM
// would start to feel it.
const MAX_ITEMS = 100;

function parseItems(payload: unknown): ListRow[] {
  if (typeof payload !== "object" || payload === null) return [];
  const items = (payload as { items?: unknown }).items;
  if (!Array.isArray(items)) return [];
  const rows: ListRow[] = [];
  for (const raw of items) {
    if (typeof raw !== "object" || raw === null) continue;
    const item = raw as Record<string, unknown>;
    if (typeof item.text !== "string" || !item.text) continue;
    rows.push({
      text: item.text,
      sub: typeof item.sub === "string" ? item.sub : undefined,
      href: typeof item.href === "string" ? item.href : undefined,
      state: typeof item.state === "string" ? item.state : undefined,
    });
    if (rows.length >= MAX_ITEMS) break;
  }
  return rows;
}

export default function List({ payload }: { payload: unknown }) {
  const items = parseItems(payload);
  if (items.length === 0) {
    return <p className="text-label text-ink-faint">Nothing here.</p>;
  }

  return (
    <ul className="flex flex-col">
      {items.map((item, index) => (
        <li
          key={index}
          className="flex items-center gap-2 border-b border-line py-1.5 last:border-b-0"
        >
          <div className="min-w-0 flex-1">
            {item.href ? (
              // href values are sanitised server-side (sanitize_href), but
              // this still renders a real <a rel="noreferrer"> and routes
              // the actual navigation through openExternalUrl — the same
              // Electron-safe path QuickLinks uses — rather than trusting
              // the browser's default link handling.
              <a
                href={item.href}
                target="_blank"
                rel="noreferrer"
                onClick={(event) => {
                  event.preventDefault();
                  openExternalUrl(item.href as string);
                }}
                className="block truncate text-body text-ink transition-colors hover:text-[var(--ac)]"
              >
                {item.text}
              </a>
            ) : (
              <p className="truncate text-body text-ink">{item.text}</p>
            )}
            {item.sub && <p className="truncate text-meta text-ink-faint">{item.sub}</p>}
          </div>
          {item.state && (
            <span className={`shrink-0 font-mono text-micro uppercase tracking-[0.08em] ${stateToneClass(item.state)}`}>
              {item.state}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
