"use client";

import { COUNTERS, compactTokens, type CounterKey } from "@/lib/agentPalette";

export interface TokenCounts {
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
}

/**
 * A 100% stacked bar over the four token counters, plus a legend.
 *
 * This exists to fix a real defect, not to decorate: the panels used to print
 * `in X · out Y` under a headline total that also includes cache-creation and
 * cache-read tokens. Cache reads are routinely the large majority of a
 * session, so the headline never matched the two numbers beneath it and read
 * as a bug. Every counter is now accounted for, and the parts visibly sum to
 * the whole.
 */
export default function TokenComposition({
  counts,
  color,
  className = "",
}: {
  counts: TokenCounts;
  /** Bar colour. Defaults to the mode accent. */
  color?: string;
  className?: string;
}) {
  const tint = color ?? "var(--ac)";
  const total = counts.total_tokens;
  const segments = COUNTERS.map((counter) => ({
    ...counter,
    value: counts[counter.key as CounterKey] ?? 0,
  })).filter((segment) => segment.value > 0);

  if (total <= 0 || segments.length === 0) return null;

  return (
    <div className={className}>
      <div
        className="flex h-2 w-full overflow-hidden bg-sunken"
        role="img"
        aria-label={segments
          .map((s) => `${s.label} ${Math.round((s.value / total) * 100)}%`)
          .join(", ")}
      >
        {segments.map((segment) => (
          <div
            key={segment.key}
            title={`${segment.label}: ${segment.value.toLocaleString()}`}
            style={{
              width: `${(segment.value / total) * 100}%`,
              background: tint,
              opacity: segment.opacity,
            }}
          />
        ))}
      </div>

      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {segments.map((segment) => (
          <li key={segment.key} className="flex items-center gap-1.5 font-mono text-micro">
            <span
              aria-hidden
              className="h-2 w-2 shrink-0"
              style={{ background: tint, opacity: segment.opacity }}
            />
            <span className="uppercase tracking-[0.1em] text-ink-faint">{segment.label}</span>
            <span
              className="tabular-nums text-ink-muted"
              title={segment.value.toLocaleString()}
            >
              {compactTokens(segment.value)}
            </span>
            <span className="tabular-nums text-ink-faint">
              {Math.round((segment.value / total) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
