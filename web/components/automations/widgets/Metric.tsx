"use client";

import { stateToneClass } from "../WidgetRenderer";

interface MetricPayload {
  label: string;
  value: unknown;
  sub?: string;
  state?: string;
}

function isMetricPayload(payload: unknown): payload is MetricPayload {
  return typeof payload === "object" && payload !== null && "value" in (payload as object);
}

/** A pushed metric's `value` is untyped JSON — most workflows send a number
 * or string, but nothing stops one from sending an object. That still needs
 * to render as *something* legible rather than crash the panel. */
function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString() : "—";
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "true" : "false";
  try {
    return JSON.stringify(value);
  } catch {
    return "—";
  }
}

export default function Metric({ payload }: { payload: unknown }) {
  if (!isMetricPayload(payload)) {
    return <p className="text-label text-ink-faint">This metric&rsquo;s payload is missing a value.</p>;
  }
  const label = typeof payload.label === "string" && payload.label ? payload.label : "METRIC";
  const sub = typeof payload.sub === "string" ? payload.sub : null;

  return (
    <div>
      <p className="font-mono text-meta uppercase tracking-[0.14em] text-ink-faint">{label}</p>
      <p
        className={`mt-1 font-mono text-title font-semibold ${
          payload.state ? stateToneClass(payload.state) : "text-ink-bright"
        }`}
      >
        {formatValue(payload.value)}
      </p>
      {sub && <p className="mt-0.5 text-label text-ink-muted">{sub}</p>}
    </div>
  );
}
