"use client";

import { useState } from "react";
import { compactTokens } from "@/lib/agentPalette";

export interface ChartSeries {
  key: string;
  label: string;
  color: string;
  /** Alpha applied to this band's fill. Stacked counters descend by cost. */
  opacity?: number;
  points: number[];
}

const VIEW_W = 600;
const VIEW_H = 160;

/**
 * The usage chart, in two modes over one implementation.
 *
 * - `stacked` — one agent, four bands (output / input / cache write / cache
 *   read). The shape of the spend is the story: a session that is 90% cache
 *   reads and one that is 90% fresh input draw the identical total line while
 *   costing wildly different amounts.
 * - unstacked — several agents, one line each, so `ALL AGENTS` is a real
 *   comparison rather than a sum that hides which tool spent what.
 *
 * Generalising to a series list rather than adding a second component keeps
 * one set of axis, hover, and tooltip behaviour — the modes cannot drift apart.
 *
 * One `<svg>`, no recharts: `/system` is inside the §10 perf budget that
 * reserves recharts for `/insights`. `preserveAspectRatio="none"` stretches the
 * plot to its container, so every stroke carries `vector-effect` to stay 1px
 * crisp and all text lives outside the scaled SVG (scaled text smears).
 */
export default function UsageAreaChart({
  series,
  labels,
  stacked = false,
  className = "h-40",
}: {
  series: ChartSeries[];
  /** One per point, used for the axis ends and the tooltip heading. */
  labels: string[];
  stacked?: boolean;
  className?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);

  const length = Math.max(0, ...series.map((s) => s.points.length));
  if (length === 0 || series.length === 0) {
    return (
      <div className={`flex items-center justify-center border border-line ${className}`}>
        <p className="font-mono text-meta text-ink-faint">no activity in this range</p>
      </div>
    );
  }

  // A single point has no line to draw, so everything is mirrored into a flat
  // pair — otherwise the chart silently renders as an empty box.
  const doubled = length === 1;
  const count = doubled ? 2 : length;
  const at = (points: number[], index: number) => points[doubled ? 0 : index] ?? 0;

  const totalAt = (index: number) => series.reduce((sum, s) => sum + at(s.points, index), 0);
  const max = Math.max(
    1,
    ...Array.from({ length: count }, (_, i) =>
      stacked ? totalAt(i) : Math.max(...series.map((s) => at(s.points, i))),
    ),
  );
  const stepX = count > 1 ? VIEW_W / (count - 1) : VIEW_W;
  const y = (value: number) => (VIEW_H - (value / max) * VIEW_H).toFixed(1);
  const x = (index: number) => (index * stepX).toFixed(1);

  // Cumulative baselines when stacked, so each band sits on the one below.
  const baselines: number[][] = [];
  let running = Array.from({ length: count }, () => 0);
  for (const entry of series) {
    baselines.push(running);
    if (stacked) {
      running = running.map((base, index) => base + at(entry.points, index));
    }
  }

  const bands = series.map((entry, order) => {
    const base = baselines[order];
    const top = Array.from({ length: count }, (_, i) => base[i] + at(entry.points, i));
    const shape = [
      ...top.map((value, i) => `${x(i)},${y(value)}`),
      ...base.map((value, i) => `${x(i)},${y(value)}`).reverse(),
    ].join(" ");
    return { entry, top, shape };
  });

  const activeLabel = hover === null ? null : (labels[doubled ? 0 : hover] ?? "");

  return (
    // Column layout with the plot flexing: the axis labels are part of the
    // stated height rather than overflowing past it, which is what made them
    // collide with anything rendered underneath.
    <div className={`relative flex flex-col ${className}`}>
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="none"
        className="min-h-0 w-full flex-1"
        role="img"
        aria-label={
          stacked
            ? "Token usage over time, split by input, output and cache"
            : `Token usage over time for ${series.map((s) => s.label).join(", ")}`
        }
      >
        {/* Quartile baselines — enough to read a magnitude off, quiet enough
            not to compete with the data. */}
        {[0.25, 0.5, 0.75].map((fraction) => (
          <line
            key={fraction}
            x1="0"
            x2={VIEW_W}
            y1={VIEW_H * fraction}
            y2={VIEW_H * fraction}
            stroke="currentColor"
            className="text-line"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        ))}

        {bands.map(({ entry, top, shape }) => (
          <g key={entry.key}>
            <polygon
              points={shape}
              fill={entry.color}
              opacity={entry.opacity ?? (stacked ? 1 : 0.12)}
            />
            {/* Unstacked series need their own outline to stay separable where
                they overlap; stacked bands read fine from the fill alone. */}
            {!stacked && (
              <polyline
                points={top.map((value, i) => `${x(i)},${y(value)}`).join(" ")}
                fill="none"
                stroke={entry.color}
                strokeWidth="1.5"
                vectorEffect="non-scaling-stroke"
              />
            )}
          </g>
        ))}

        {stacked && (
          <polyline
            points={Array.from({ length: count }, (_, i) => `${x(i)},${y(totalAt(i))}`).join(" ")}
            fill="none"
            stroke={series[0]?.color ?? "var(--ac)"}
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
          />
        )}

        {hover !== null && (
          <line
            x1={x(hover)}
            x2={x(hover)}
            y1="0"
            y2={VIEW_H}
            stroke="currentColor"
            className="text-ink-muted"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        )}

        {/* Invisible hit strips: one per point, so hovering is forgiving at any
            container width without a mousemove-to-index calculation. */}
        {Array.from({ length: count }, (_, index) => (
          <rect
            key={index}
            x={Math.max(0, index * stepX - stepX / 2)}
            y="0"
            width={stepX}
            height={VIEW_H}
            fill="transparent"
            onMouseEnter={() => setHover(index)}
            onMouseLeave={() => setHover((current) => (current === index ? null : current))}
          />
        ))}
      </svg>

      {hover !== null && (
        <div
          className="pointer-events-none absolute top-1 z-10 min-w-[9rem] border border-lineHi bg-panel px-2 py-1.5 shadow-lg"
          style={{
            left: `${(hover / Math.max(1, count - 1)) * 100}%`,
            transform: `translateX(${hover > count / 2 ? "-105%" : "5%"})`,
          }}
        >
          <p className="font-mono text-micro uppercase tracking-[0.12em] text-ink-faint">
            {activeLabel}
          </p>
          <p className="mt-0.5 font-mono text-label text-ink-bright">
            {totalAt(hover).toLocaleString()}
          </p>
          <ul className="mt-1 space-y-0.5">
            {series.map((entry) => {
              const value = at(entry.points, hover);
              if (value === 0) return null;
              return (
                <li
                  key={entry.key}
                  className="flex items-center gap-1.5 font-mono text-micro text-ink-muted"
                >
                  <span
                    aria-hidden
                    className="h-1.5 w-1.5 shrink-0"
                    style={{ background: entry.color, opacity: entry.opacity ?? 1 }}
                  />
                  <span className="flex-1 truncate">{entry.label}</span>
                  <span className="tabular-nums">{compactTokens(value)}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* Axis labels sit outside the stretched SVG so they are not smeared.
          Read defensively: `labels` is a separate array from `series`, so a
          caller that lets them disagree used to render a literal "undefined"
          at the end of the axis rather than nothing. */}
      <div className="mt-1 flex shrink-0 justify-between font-mono text-micro text-ink-faint">
        <span>{labels[0] ?? ""}</span>
        {labels.length > 1 && <span>{labels[labels.length - 1] ?? ""}</span>}
      </div>
    </div>
  );
}
