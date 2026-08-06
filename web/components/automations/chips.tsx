import type { CSSProperties } from "react";

/**
 * Shared presentational chips for automations surfaces — origin attribution,
 * freshness/state badges, the AUTO provenance tag, the display/action kind
 * marker, and the basic-auth notice.
 *
 * Pure — no data fetching, no state, no side effects. Four later chunks
 * (dashboard widgets, the command palette, the activity log, the template
 * gallery) all render these, so the visual vocabulary for "where did this
 * come from" and "what state is it in" lives once, here, instead of drifting
 * across four inline copies.
 *
 * House rule inherited from the rest of the app: everything is square —
 * `borderRadius` in tailwind.config.ts overrides the whole scale so `rounded`
 * is a no-op. Nothing here adds rounding back.
 */

// ---------------------------------------------------------------------------
// OriginChip

/**
 * Fixed pool of per-instance colours, in allocation order. `#a78bfa` doubles
 * as the general mode accent on purpose — the first-registered n8n instance
 * reads as "the" instance, unadorned, when there's only one and this chip is
 * hidden anyway. Chosen to stay legible at `opacity-85` on `panel`/`void` and
 * distinct from the semantic tokens (`ok`/`warn`/`danger`).
 *
 * Deliberately NOT added to tailwind.config.ts: this pool is data-driven
 * (indexed by a hash of a runtime instance id), not a semantic design token
 * like `ok` or `warn` — the brief calls this out as the one legitimate
 * exception to "every colour must be a token."
 */
const ORIGIN_COLORS = [
  "#a78bfa",
  "#60a5fa",
  "#e879f9",
  "#34d399",
  "#fbbf24",
  "#22d3ee",
] as const;

/**
 * Deterministic instance -> colour mapping. The same `instanceId` always
 * resolves to the same colour, this load and the next, with nothing stored —
 * just a stable hash into a fixed pool. Exported on its own because callers
 * that need the raw colour outnumber callers that want the whole chip: an
 * activity-log timeline dot, a palette section header rule, etc.
 */
export function originColor(instanceId: string): string {
  let hash = 0;
  for (let i = 0; i < instanceId.length; i++) {
    hash = (Math.imul(31, hash) + instanceId.charCodeAt(i)) | 0;
  }
  return ORIGIN_COLORS[Math.abs(hash) % ORIGIN_COLORS.length];
}

/**
 * Which n8n instance a widget/row came from. Renders `null` when `name` is
 * empty/undefined — the caller decides by omitting `name` (typically because
 * only one instance is registered, and a chip that always says the same
 * thing is noise, not information).
 */
export function OriginChip({
  instanceId,
  name,
}: {
  instanceId: string;
  name?: string | null;
}) {
  if (!name) return null;
  const color = originColor(instanceId);
  return (
    <span
      className="border px-1 py-px font-mono text-micro uppercase tracking-[0.1em] opacity-85"
      style={{ borderColor: color, color }}
      title={`From n8n instance: ${name}`}
    >
      {name}
    </span>
  );
}

// ---------------------------------------------------------------------------
// StateBadge

export type WidgetState = "live" | "stale" | "empty" | "waiting";

/**
 * Widget freshness/state chip. See the state doc in `WidgetShell.tsx` for why
 * there are four states and not two: LIVE and EMPTY are both healthy, and
 * neither gets a badge here on purpose — a badge on a healthy panel is noise,
 * and EMPTY gets a body-level treatment (a later chunk) instead of a chip.
 * The label text always carries the meaning on its own (`STALE 4h`,
 * `WAITING`) — colour is never the only channel.
 */
export function StateBadge({
  state,
  age,
}: {
  state: WidgetState;
  /** Pre-formatted relative age, e.g. "4h". Omit if unknown. */
  age?: string | null;
}) {
  if (state === "live" || state === "empty") return null;

  if (state === "stale") {
    const label = age ? `STALE ${age}` : "STALE";
    return (
      <span
        className="border border-warn px-1 py-px font-mono text-micro uppercase tracking-[0.14em] text-warn"
        title={age ? `Stale — last update ${age} ago, past its expected cadence` : "Stale — past its expected update cadence"}
      >
        {label}
      </span>
    );
  }

  // waiting
  return (
    <span
      className="border border-auto-line px-1 py-px font-mono text-micro uppercase tracking-[0.14em] text-auto"
      title="Installed, but no data has arrived yet"
    >
      WAITING
    </span>
  );
}

// ---------------------------------------------------------------------------
// AutoChip

/**
 * The AUTO provenance tag: this panel/row is fed by an automation (n8n)
 * rather than a native Argus source. Single source of truth for the markup —
 * `WidgetShell.tsx` currently inlines an equivalent chip; a later chunk
 * swaps that inline copy for this component so the two cannot drift.
 */
export function AutoChip() {
  return (
    <span
      className="border border-auto-line px-1 py-px font-mono text-micro uppercase tracking-[0.14em] text-auto"
      title="Fed by an automation (n8n), not a native Argus source"
    >
      AUTO
    </span>
  );
}

// ---------------------------------------------------------------------------
// KindChip

export type WidgetKind = "display" | "action";

const KIND_LABEL: Record<WidgetKind, string> = {
  display: "DISPLAY",
  action: "ACTION",
};

const KIND_STYLE: Record<WidgetKind, CSSProperties> = {
  // var(--ac) — the live mode accent. Display widgets are the common case,
  // so they get the same colour the rest of the mode's chrome already uses.
  display: { borderColor: "var(--ac)", color: "var(--ac)" },
  // Not a token: distinct from every mode accent on purpose, since ACTION
  // must read the same regardless of which mode the user is in.
  action: { borderColor: "#60a5fa", color: "#60a5fa" },
};

const KIND_TITLE: Record<WidgetKind, string> = {
  display: "Displays data only",
  action: "Can trigger a workflow",
};

/**
 * Whether a widget only displays data (`display`) or can trigger a workflow
 * (`action`). Fixed width so a column of these — a template gallery list, a
 * palette results list — lines up regardless of label length.
 */
export function KindChip({ kind }: { kind: WidgetKind }) {
  return (
    <span
      className="inline-flex w-20 items-center justify-center border px-1 py-px font-mono text-micro uppercase tracking-[0.14em]"
      style={KIND_STYLE[kind]}
      title={KIND_TITLE[kind]}
    >
      {KIND_LABEL[kind]}
    </span>
  );
}

// ---------------------------------------------------------------------------
// ActionStateBadge

export type ActionState = "ready" | "running" | "failing" | "never-run";

const ACTION_STATE_STYLE: Record<ActionState, string> = {
  ready: "border-ok text-ok",
  running: "border-warn text-warn",
  failing: "border-danger text-danger",
  "never-run": "border-line text-ink-faint",
};

const ACTION_STATE_LABEL: Record<ActionState, string> = {
  ready: "READY",
  running: "RUNNING",
  failing: "FAILING",
  "never-run": "NEVER RUN",
};

/**
 * An action workflow's health, derived from its own `last_run`. The action
 * counterpart to `StateBadge` (which speaks for a display's freshness) —
 * unlike `StateBadge`, this always renders: a workflow that has never run is
 * exactly as worth saying as one that's failing, not a healthy default.
 */
export function ActionStateBadge({ state }: { state: ActionState }) {
  return (
    <span
      className={`shrink-0 border px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.1em] ${ACTION_STATE_STYLE[state]}`}
    >
      {ACTION_STATE_LABEL[state]}
    </span>
  );
}

// ---------------------------------------------------------------------------
// AuthChip

/**
 * Marks a form trigger protected by basic auth. The credential itself is
 * resolved from the OS keyring server-side and never appears in the URL —
 * the `title` exists so that guarantee is discoverable at a glance, not just
 * true in the backend.
 */
export function AuthChip() {
  return (
    <span
      className="border border-auto-line px-1 py-px font-mono text-micro uppercase tracking-[0.14em] text-auto"
      title="Protected by basic auth — the credential is resolved from the OS keyring server-side and never placed in the URL"
    >
      ⌁ AUTH
    </span>
  );
}
