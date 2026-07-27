"use client";

import { useState } from "react";
import { COUNTERS, compactTokens, type CounterKey } from "@/lib/agentPalette";

export interface UsageAreaPoint {
  label: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
}

const VIEW_W = 600;
const VIEW_H = 160;

/**
 * Stacked-area usage chart. One `<svg>`, no recharts — `/system` is inside the
 * §10 perf budget that reserves recharts for `/insights`.
 *
 * Stacked rather than a single total line because the shape of the spend is
 * the story: a session that is 90% cache reads and one that is 90% fresh input
 * cost wildly different amounts while drawing the identical total line.
 *
 * `preserveAspectRatio="none"` stretches the plot to the container, so every
 * stroke carries `vector-effect="non-scaling-stroke"` to stay 1px crisp, and
 * text lives outside the scaled `<svg>` rather than inside it (scaled text
 * would smear horizontally).
 */
export default function UsageAreaChart({
  points,
  color,
  className = "h-40",
}: {
  points: UsageAreaPoint[];
  /** Series colour. Defaults to the mode accent. */
  color?: string;
  className?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const stroke = color ?? "var(--ac)";

  if (points.length === 0) {
    return (
      <div className={`flex items-center justify-center border border-line ${className}`}>
        <p className="font-mono text-meta text-ink-faint">no activity in this range</p>
      </div>
    );
  }

  // A single point has no line to draw, so it is mirrored into a flat pair —
  // otherwise the chart silently renders as an empty box.
  const series = points.length === 1 ? [points[0], points[0]] : points;
  const max = Math.max(1, ...series.map((point) => point.total_tokens));
  const stepX = series.length > 1 ? VIEW_W / (series.length - 1) : VIEW_W;

  // Cumulative baselines, drawn back-to-front so each band sits on the one below.
  let baseline = series.map(() => 0);
  const bands = COUNTERS.map((counter) => {
    const top = series.map(
      (point, index) => baseline[index] + (point[counter.key as CounterKey] as number),
    );
    const shape = [
      ...top.map((value, index) => `${(index * stepX).toFixed(1)},${y(value)}`),
      ...baseline
        .map((value, index) => `${(index * stepX).toFixed(1)},${y(value)}`)
        .reverse(),
    ].join(" ");
    baseline = top;
    return { ...counter, shape, top };
  });

  function y(value: number): string {
    return (VIEW_H - (value / max) * VIEW_H).toFixed(1);
  }

  const active = hover === null ? null : series[hover];

  return (
    <div className={`relative ${className}`}>
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="none"
        className="h-full w-full"
        role="img"
        aria-label={`Token usage over ${points.length} periods, split by input, output and cache`}
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

        {bands.map((band) => (
          <polygon key={band.key} points={band.shape} fill={stroke} opacity={band.opacity} />
        ))}

        {/* The total, so the silhouette stays legible over the faintest band. */}
        <polyline
          points={series
            .map((point, index) => `${(index * stepX).toFixed(1)},${y(point.total_tokens)}`)
            .join(" ")}
          fill="none"
          stroke={stroke}
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
        />

        {hover !== null && (
          <line
            x1={hover * stepX}
            x2={hover * stepX}
            y1="0"
            y2={VIEW_H}
            stroke={stroke}
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
            opacity="0.8"
          />
        )}

        {/* Invisible hit strips: one per point, so hovering is forgiving at any
            container width without a mousemove-to-index calculation. */}
        {series.map((point, index) => (
          <rect
            key={`${point.label}-${index}`}
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

      {active && (
        <div
          className="pointer-events-none absolute top-1 z-10 min-w-[8.5rem] border border-lineHi bg-panel px-2 py-1.5 shadow-lg"
          style={{
            left: `${((hover ?? 0) / Math.max(1, series.length - 1)) * 100}%`,
            transform: `translateX(${(hover ?? 0) > series.length / 2 ? "-105%" : "5%"})`,
          }}
        >
          <p className="font-mono text-micro uppercase tracking-[0.12em] text-ink-faint">
            {active.label}
          </p>
          <p className="mt-0.5 font-mono text-label text-ink-bright">
            {active.total_tokens.toLocaleString()}
          </p>
          <ul className="mt-1 space-y-0.5">
            {COUNTERS.map((counter) => {
              const value = active[counter.key as CounterKey] as number;
              if (value === 0) return null;
              return (
                <li
                  key={counter.key}
                  className="flex items-center gap-1.5 font-mono text-micro text-ink-muted"
                >
                  <span
                    aria-hidden
                    className="h-1.5 w-1.5 shrink-0"
                    style={{ background: stroke, opacity: counter.opacity }}
                  />
                  <span className="flex-1">{counter.label}</span>
                  <span className="tabular-nums">{compactTokens(value)}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* Axis labels sit outside the stretched SVG so they are not smeared. */}
      <div className="mt-1 flex justify-between font-mono text-micro text-ink-faint">
        <span>{points[0].label}</span>
        {points.length > 1 && <span>{points[points.length - 1].label}</span>}
      </div>
    </div>
  );
}
