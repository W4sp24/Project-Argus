import Link from "next/link";
import MiniLineChart from "@/components/charts/MiniLineChart";

export interface UsageBlockPoint {
  label: string;
  value: number;
}

export interface UsageBlockRow {
  label: string;
  value: number;
}

export interface CapBar {
  /** Short label rendered above the bar, e.g. "5H WINDOW" or "WEEKLY". Omit for the single-bar case (ARGUS.USAGE) to keep that panel's existing look unchanged (no label line was there before). */
  label?: string;
  /** 0-100, or null if no cap is configured (renders a "not set" link instead of a bar). */
  pctOfCap: number | null;
  /** Optional caption suffix appended after the percentage, e.g. "of ~5h estimate" instead of the default "of soft cap". */
  capNoun?: string;
  /** Where the "not set →" link should point when pctOfCap is null. Defaults to "/system". */
  configureHref?: string;
}

function capBarColor(pct: number): string {
  return pct >= 90 ? "bg-danger" : pct >= 75 ? "bg-amber-400" : "bg-[var(--ac)]";
}

function capBarCaption(pct: number, noun: string): string {
  let caption = `${pct}% ${noun}`;
  if (pct >= 90) caption += pct === 100 ? " · at limit" : " · near limit";
  else if (pct >= 75) caption += " · high";
  return caption;
}

interface UsageBlockProps {
  isLoading: boolean;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  estimatedCostUsd: number;
  /** One progress bar per entry, rendered in order. Omit to skip the cap-bar section entirely (e.g. CLI usage previously had no configured cap). */
  capBars?: CapBar[];
  series: UsageBlockPoint[];
  rows: UsageBlockRow[];
  emptyMessage?: string;
  /** "wide": a 3-column layout (stats | bigger chart | breakdown) for a full-width panel. Default: stacked. */
  size?: "default" | "wide";
}

/**
 * Shared "total + in/out/cost line + optional cap bar + mini chart +
 * breakdown list" rendering, reused by ARGUS.USAGE (app usage, breakdown by
 * feature) and CLAUDE CODE (account-wide CLI usage, breakdown by model).
 */
export default function UsageBlock({
  isLoading,
  totalTokens,
  inputTokens,
  outputTokens,
  estimatedCostUsd,
  capBars,
  series,
  rows,
  emptyMessage = "no usage recorded yet",
  size = "default",
}: UsageBlockProps) {
  const hasData = totalTokens > 0;

  if (isLoading && !hasData) {
    return <p className="text-[12.5px] text-ink-faint">loading usage…</p>;
  }
  if (!hasData) {
    return <p className="text-[12.5px] text-ink-faint">{emptyMessage}</p>;
  }

  const stats = (
    <div>
      <p
        className={`font-mono font-semibold text-ink-bright ${size === "wide" ? "text-4xl" : "text-2xl"}`}
      >
        {totalTokens.toLocaleString()}
        <span className="ml-1.5 text-xs font-normal text-ink-faint">tokens</span>
      </p>
      <p className="mt-1 font-mono text-[11px] text-ink-muted">
        in {inputTokens.toLocaleString()} · out {outputTokens.toLocaleString()} · ≈$
        {estimatedCostUsd.toFixed(2)}
      </p>

      {capBars?.map((bar, index) => (
        <div key={bar.label ?? index} className="mt-2">
          {bar.label && (
            <p className="mb-1 font-mono text-[9px] uppercase tracking-[0.12em] text-ink-faint">
              {bar.label}
            </p>
          )}
          {bar.pctOfCap === null ? (
            <Link
              href={bar.configureHref ?? "/system"}
              className="font-mono text-[10px] uppercase tracking-wide text-ink-faint hover:text-[var(--ac)]"
            >
              NOT SET →
            </Link>
          ) : (
            <>
              <div className="h-1 w-full bg-sunken">
                <div
                  className={`h-1 ${capBarColor(bar.pctOfCap)}`}
                  style={{ width: `${bar.pctOfCap}%` }}
                />
              </div>
              <p className="mt-1 font-mono text-[10px] text-ink-faint">
                {capBarCaption(bar.pctOfCap, bar.capNoun ?? "of soft cap")}
              </p>
            </>
          )}
        </div>
      ))}
    </div>
  );

  const chart = (
    <MiniLineChart
      values={series.map((point) => point.value)}
      labels={series.map((point) => point.label)}
      className={size === "wide" ? "h-32" : "h-16"}
    />
  );

  const breakdown = (
    <ul className={size === "wide" ? "space-y-1.5" : "mt-3 space-y-1 border-t border-line pt-2"}>
      {rows.map((row) => (
        <li key={row.label} className="flex items-center justify-between font-mono text-[11px]">
          <span className="uppercase tracking-wide text-ink-faint">{row.label}</span>
          <span className="text-ink-muted">{row.value.toLocaleString()}</span>
        </li>
      ))}
    </ul>
  );

  if (size === "wide") {
    return (
      <div className="grid gap-8 lg:grid-cols-[minmax(0,220px)_minmax(0,1fr)_minmax(0,200px)]">
        {stats}
        <div className="flex flex-col justify-center">{chart}</div>
        {breakdown}
      </div>
    );
  }

  return (
    <>
      {stats}
      <div className="mt-3">{chart}</div>
      {breakdown}
    </>
  );
}
