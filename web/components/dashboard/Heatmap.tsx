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

function hex(n: number): string {
  return Math.round(n).toString(16).padStart(2, "0");
}

/** Linear RGB interpolation between the ramp endpoints (§4: #100c1e → #c4b5fd). */
function rampColor(t: number): string {
  const from = { r: 0x10, g: 0x0c, b: 0x1e };
  const to = { r: 0xc4, g: 0xb5, b: 0xfd };
  const r = from.r + (to.r - from.r) * t;
  const g = from.g + (to.g - from.g) * t;
  const b = from.b + (to.b - from.b) * t;
  return `#${hex(r)}${hex(g)}${hex(b)}`;
}

const RAMP = [0, 0.25, 0.5, 0.75, 1].map(rampColor);

export default function Heatmap({ className = "" }: { className?: string }) {
  const { data, error } = useHeatmap();
  const [metric, setMetric] = useState<Metric>("all");
  const [hover, setHover] = useState<HeatmapDay | null>(null);

  const { weeks, max, streak, weekTotal, best } = useMemo(() => {
    const days = data?.days ?? [];
    // Pad the front so columns start on Sunday.
    const lead = days.length ? new Date(`${days[0].date}T00:00:00`).getDay() : 0;
    const cells: (HeatmapDay | null)[] = [...Array(lead).fill(null), ...days];
    const weeks: (HeatmapDay | null)[][] = [];
    for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
    const max = Math.max(0, ...days.map((day) => countFor(day, metric)));

    let streak = 0;
    for (let i = days.length - 1; i >= 0; i -= 1) {
      if (countFor(days[i], metric) > 0) streak += 1;
      else break;
    }
    const weekTotal = days.slice(-7).reduce((sum, day) => sum + countFor(day, metric), 0);
    const best = days.reduce<HeatmapDay | null>(
      (top, day) => (!top || countFor(day, metric) > countFor(top, metric) ? day : top),
      null,
    );

    return { weeks, max, streak, weekTotal, best: best && countFor(best, metric) > 0 ? best : null };
  }, [data, metric]);

  return (
    <Panel
      label="ACTIVITY.HEATMAP"
      className={className}
      headerRight={
        <p className="font-mono text-[11px] text-ink-faint">
          {hover
            ? `${hover.date} · ${hover.tasks}t ${hover.notes}n ${hover.study}s ${hover.captures}c`
            : "hover a day"}
        </p>
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        {METRICS.map((option) => (
          <button
            key={option}
            onClick={() => setMetric(option)}
            className={`border border-line px-2 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors ${
              metric === option
                ? "bg-[var(--ac-bg)] text-[var(--ac)]"
                : "text-ink-faint hover:border-lineHi hover:text-ink-muted"
            }`}
          >
            {option}
          </button>
        ))}
      </div>
      {error && <p className="text-sm text-ink-muted">Couldn&apos;t load activity — is the backend up?</p>}

      <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
        <div className="min-w-0 overflow-x-auto pb-1" data-testid="heatmap">
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

        <div className="flex shrink-0 flex-col gap-3 border-line lg:w-44 lg:border-l lg:pl-5">
          <div>
            <p className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-ink-faint">streak</p>
            <p className="font-mono text-lg font-semibold text-ink-bright">{streak}d</p>
          </div>
          <div>
            <p className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-ink-faint">this week</p>
            <p className="font-mono text-lg font-semibold text-ink-bright">{weekTotal}</p>
          </div>
          <div>
            <p className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-ink-faint">best day</p>
            <p className="font-mono text-[11px] text-ink-muted">
              {best ? `${best.date} · ${countFor(best, metric)}` : "—"}
            </p>
          </div>
          <div className="flex items-center gap-1 pt-1">
            <span className="font-mono text-[9.5px] text-ink-faint">less</span>
            {RAMP.map((color, i) => (
              <span key={i} className="h-2.5 w-2.5" style={{ background: color }} />
            ))}
            <span className="font-mono text-[9.5px] text-ink-faint">more</span>
          </div>
        </div>
      </div>
    </Panel>
  );
}
