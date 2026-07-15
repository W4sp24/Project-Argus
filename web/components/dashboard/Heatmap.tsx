"use client";

import { useMemo, useState } from "react";
import Panel from "@/components/Panel";
import { HeatmapDay, useHeatmap } from "@/lib/api";

const METRICS = ["all", "tasks", "notes", "study", "captures"] as const;
type Metric = (typeof METRICS)[number];

const CELL = 11;
const GAP = 3;

function countFor(day: HeatmapDay, metric: Metric): number {
  return metric === "all" ? day.total : day[metric];
}

/** 0–4 intensity on the purple ramp, quantized against the visible max. */
function level(count: number, max: number): number {
  if (count === 0 || max === 0) return 0;
  return Math.min(4, Math.ceil((count / max) * 4));
}

const RAMP = [
  "rgba(255,255,255,0.05)",
  "rgba(139,92,246,0.25)",
  "rgba(139,92,246,0.45)",
  "rgba(167,139,250,0.7)",
  "rgba(196,181,253,0.95)",
];

export default function Heatmap() {
  const { data, error } = useHeatmap();
  const [metric, setMetric] = useState<Metric>("all");
  const [hover, setHover] = useState<HeatmapDay | null>(null);

  const { weeks, max } = useMemo(() => {
    const days = data?.days ?? [];
    // Pad the front so columns start on Sunday.
    const lead = days.length ? new Date(`${days[0].date}T00:00:00`).getDay() : 0;
    const cells: (HeatmapDay | null)[] = [...Array(lead).fill(null), ...days];
    const weeks: (HeatmapDay | null)[][] = [];
    for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
    const max = Math.max(0, ...days.map((day) => countFor(day, metric)));
    return { weeks, max };
  }, [data, metric]);

  return (
    <Panel label="ACTIVITY" title="A year at a glance">
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        {METRICS.map((option) => (
          <button
            key={option}
            onClick={() => setMetric(option)}
            className={`rounded-lg px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors ${
              metric === option
                ? "bg-primary/25 text-primary-soft"
                : "text-ink-faint hover:bg-white/5 hover:text-ink-muted"
            }`}
          >
            {option}
          </button>
        ))}
      </div>
      {error && <p className="text-sm text-ink-muted">Couldn’t load activity — is the backend up?</p>}
      <div className="overflow-x-auto pb-1" data-testid="heatmap">
        <svg
          width={weeks.length * (CELL + GAP)}
          height={7 * (CELL + GAP)}
          role="img"
          aria-label="Productivity heatmap, one cell per day"
        >
          {weeks.map((week, x) =>
            week.map(
              (day, y) =>
                day && (
                  <rect
                    key={day.date}
                    x={x * (CELL + GAP)}
                    y={y * (CELL + GAP)}
                    width={CELL}
                    height={CELL}
                    rx={2.5}
                    fill={RAMP[level(countFor(day, metric), max)]}
                    data-date={day.date}
                    data-count={countFor(day, metric)}
                    onMouseEnter={() => setHover(day)}
                    onMouseLeave={() => setHover(null)}
                  >
                    <title>{`${day.date} — ${day.total} events`}</title>
                  </rect>
                ),
            ),
          )}
        </svg>
      </div>
      <p className="mt-2 h-4 font-mono text-[11px] text-ink-faint">
        {hover
          ? `${hover.date}: ${hover.tasks} tasks · ${hover.notes} notes · ${hover.study} study · ${hover.captures} captures`
          : "hover a day for the breakdown"}
      </p>
    </Panel>
  );
}
