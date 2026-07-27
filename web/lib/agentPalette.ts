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
  "claude-subagents": "#f0b354", // amber — same family as Claude Code, clearly apart
  codex: "#5eead4", // teal
};

/**
 * Colour for an agent the user registered.
 *
 * Hashed from the id rather than assigned on creation, so an agent keeps its
 * colour across machines and across a delete-and-re-add, and nothing has to be
 * stored to make that true. Saturation and lightness are fixed at values that
 * read on `#0c0916`; only the hue varies.
 */
function hashedColor(id: string): string {
  let hash = 0;
  for (let index = 0; index < id.length; index += 1) {
    hash = (hash * 31 + id.charCodeAt(index)) % 360;
  }
  return `hsl(${hash} 62% 63%)`;
}

export function agentColor(id: string): string {
  return AGENT_COLORS[id] ?? hashedColor(id);
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
