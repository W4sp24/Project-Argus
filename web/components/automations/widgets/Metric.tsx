"use client";

import { stateToneClass } from "../WidgetRenderer";

interface MetricPayload {
  label: string;
  value: unknown;
  sub?: string;
  state?: string;
  /** Presentation only. `"pill"` renders the value as a bordered chip instead
   * of a large numeral — the shape a status widget wants ("3 NEW", "ARMED")
   * where the value is a short token rather than a quantity.
   *
   * The design called this a seventh renderer, `status`. It ships here
   * instead, because a pill plus a sub-line is metric-shaped: adding a real
   * seventh kind would mean widening the `kind` CHECK constraint on
   * automation_widgets, and SQLite cannot alter a CHECK — a whole table
   * rebuild for a purely visual difference.
   *
   * The trigger is explicit and declared by the payload. It is deliberately
   * NOT inferred from the value's length or content: a metric that silently
   * changed shape when a number happened to get short would be worse than
   * having no variant at all. */
  display?: string;
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
  const tone = payload.state ? stateToneClass(payload.state) : "text-ink-bright";
  const pill = payload.display === "pill";

  return (
    <div>
      <p className="font-mono text-meta uppercase tracking-[0.14em] text-ink-faint">{label}</p>
      {pill ? (
        <p className="mt-2">
          <span
            className={`inline-block border border-current px-2 py-0.5 font-mono text-label tracking-[0.1em] ${tone}`}
          >
            {formatValue(payload.value)}
          </span>
        </p>
      ) : (
        <p className={`mt-1 font-mono text-title font-semibold ${tone}`}>
          {formatValue(payload.value)}
        </p>
      )}
      {sub && <p className="mt-0.5 text-label text-ink-muted">{sub}</p>}
    </div>
  );
}
