import MiniLineChart from "@/components/charts/MiniLineChart";

export interface UsageBlockPoint {
  label: string;
  value: number;
}

export interface UsageBlockRow {
  label: string;
  value: number;
}

interface UsageBlockProps {
  isLoading: boolean;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  estimatedCostUsd: number;
  /** Percent of a soft cap (0-100). Omit to skip the cap bar entirely (e.g. CLI usage has no configured cap). */
  pctOfCap?: number;
  series: UsageBlockPoint[];
  rows: UsageBlockRow[];
  emptyMessage?: string;
}

/**
 * Shared "total + in/out/cost line + optional cap bar + mini chart +
 * breakdown list" rendering, reused by TOKENS.CLAUDE (app usage, breakdown by
 * feature) and CLAUDE CODE (account-wide CLI usage, breakdown by model).
 */
export default function UsageBlock({
  isLoading,
  totalTokens,
  inputTokens,
  outputTokens,
  estimatedCostUsd,
  pctOfCap,
  series,
  rows,
  emptyMessage = "no usage recorded yet",
}: UsageBlockProps) {
  const hasData = totalTokens > 0;

  if (isLoading && !hasData) {
    return <p className="text-[12.5px] text-ink-faint">loading usage…</p>;
  }
  if (!hasData) {
    return <p className="text-[12.5px] text-ink-faint">{emptyMessage}</p>;
  }

  return (
    <>
      <p className="font-mono text-2xl font-semibold text-ink-bright">
        {totalTokens.toLocaleString()}
        <span className="ml-1.5 text-xs font-normal text-ink-faint">tokens</span>
      </p>
      <p className="mt-1 font-mono text-[11px] text-ink-muted">
        in {inputTokens.toLocaleString()} · out {outputTokens.toLocaleString()} · ≈$
        {estimatedCostUsd.toFixed(2)}
      </p>

      {pctOfCap !== undefined && (
        <div className="mt-2">
          <div className="h-1 w-full bg-sunken">
            <div className="h-1 bg-[var(--ac)]" style={{ width: `${pctOfCap}%` }} />
          </div>
          <p className="mt-1 font-mono text-[10px] text-ink-faint">{pctOfCap}% of soft cap</p>
        </div>
      )}

      <div className="mt-3">
        <MiniLineChart
          values={series.map((point) => point.value)}
          labels={series.map((point) => point.label)}
        />
      </div>

      <ul className="mt-3 space-y-1 border-t border-line pt-2">
        {rows.map((row) => (
          <li key={row.label} className="flex items-center justify-between font-mono text-[11px]">
            <span className="uppercase tracking-wide text-ink-faint">{row.label}</span>
            <span className="text-ink-muted">{row.value.toLocaleString()}</span>
          </li>
        ))}
      </ul>
    </>
  );
}
