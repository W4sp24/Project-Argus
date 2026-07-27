/**
 * Colours for the multi-agent usage panel.
 *
 * These live in TypeScript rather than `tailwind.config.ts` on purpose. Every
 * consumer needs a *runtime* value — SVG `fill`/`stroke`, a legend dot whose
 * colour is chosen by agent id — and a dynamic class name like
 * `text-agent-${id}` is exactly the shape Tailwind's JIT purge cannot see, so
 * it would compile away. One exported map is the honest source of truth.
 *
 * Agents are categorical, so they get distinct hues. The four token counters
 * are *not* categorical — they are one quantity split by kind — so they share
 * the current mode accent at descending opacity, ordered by cost per token.
 * Visual weight then tracks spend rather than volume, which matters because
 * cache reads are usually the overwhelming majority of the tokens and close to
 * the cheapest thing on the bill.
 */

export const AGENT_COLORS: Record<string, string> = {
  "claude-code": "#e8845c", // warm coral
  codex: "#5eead4", // teal
};

/** Fallback for an agent added to the backend before it is styled here. */
export const AGENT_FALLBACK_COLOR = "#9d8fc7";

export function agentColor(id: string): string {
  return AGENT_COLORS[id] ?? AGENT_FALLBACK_COLOR;
}

/** The four counters, most expensive per token first. */
export const COUNTERS = [
  { key: "output_tokens", label: "output", opacity: 1 },
  { key: "input_tokens", label: "input", opacity: 0.62 },
  { key: "cache_creation_input_tokens", label: "cache write", opacity: 0.34 },
  { key: "cache_read_input_tokens", label: "cache read", opacity: 0.16 },
] as const;

export type CounterKey = (typeof COUNTERS)[number]["key"];

/** `1_240_000 → "1.24M"`. Full precision stays in the title attribute. */
export function compactTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 100_000 ? 0 : 1)}k`;
  return String(value);
}
