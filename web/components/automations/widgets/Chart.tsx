"use client";

import MiniLineChart from "@/components/charts/MiniLineChart";

interface Series {
  label?: unknown;
  points?: unknown;
}

/**
 * A pushed chart.
 *
 * Reuses `MiniLineChart` — a single inline SVG — rather than recharts, which
 * the perf budget reserves for `/insights`. A dashboard widget that a
 * workflow can add at any time must not drag a charting library into the
 * main bundle.
 *
 * `points` is `[[x, y], ...]` on the wire. Only `y` is plotted: the mini
 * chart is a shape, not an analysis surface, and inventing an x-axis for
 * arbitrary pushed data would promise precision this rendering does not have.
 */
function numbersFrom(points: unknown): number[] {
  if (!Array.isArray(points)) return [];
  return points
    .map((point) => {
      if (Array.isArray(point)) return Number(point[1]);
      if (typeof point === "number") return point;
      return Number.NaN;
    })
    .filter((value) => Number.isFinite(value));
}

export default function Chart({ payload }: { payload: unknown }) {
  const raw = (payload ?? {}) as { series?: unknown };
  const series = Array.isArray(raw.series) ? (raw.series as Series[]) : null;

  if (series === null) {
    return <p className="text-label text-ink-faint">This chart&rsquo;s payload has no series.</p>;
  }

  const drawable = series
    .map((entry) => ({
      label: typeof entry.label === "string" ? entry.label : "",
      values: numbersFrom(entry.points),
    }))
    .filter((entry) => entry.values.length > 1);

  if (drawable.length === 0) {
    return <p className="text-label text-ink-faint">Not enough data points to plot yet.</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {drawable.slice(0, 4).map((entry, index) => (
        <div key={index}>
          {entry.label && (
            <p className="mb-1 font-mono text-meta uppercase tracking-[0.14em] text-ink-faint">
              {entry.label}
            </p>
          )}
          <MiniLineChart values={entry.values} className="h-16" />
        </div>
      ))}
    </div>
  );
}
