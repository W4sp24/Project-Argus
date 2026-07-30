"use client";

/**
 * The shared segmented switcher (TODAY|WEEK|ALL, SESSION|WEEK|ALL, …).
 *
 * Extracted from `AgentUsage` and `TokenUsage`, which carried byte-identical
 * markup — only the option list and labels differed. `min-h-8` is deliberate:
 * it matches `Button`'s `size="sm"` (32px), so this control sits flush with
 * any button placed next to it in a `Panel` `headerRight` instead of reading
 * a type-step smaller and ~6px shorter, which is what made `+ ADD AGENT` look
 * oversized next to the old un-sized switcher.
 *
 * Generic over the option type so each caller keeps its own typed union
 * (`CliUsageRange`, `UsageRange`, …) rather than widening to `string`.
 *
 * No `focus:outline-none` here — see `web/components/ui/Button.tsx:12-16` for
 * why that class is banned on interactive elements in this repo.
 */
export default function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  labels,
}: {
  options: readonly T[];
  value: T;
  onChange: (value: T) => void;
  labels: Record<T, string>;
}) {
  return (
    <div className="flex min-h-8 border border-line font-mono text-micro uppercase tracking-[0.14em]">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onChange(option)}
          aria-pressed={value === option}
          className={`border-l border-line px-1.5 py-1 first:border-l-0 transition-colors ${
            value === option
              ? "bg-[var(--ac-bg)] text-[var(--ac)]"
              : "text-ink-faint hover:text-ink-muted"
          }`}
        >
          {labels[option]}
        </button>
      ))}
    </div>
  );
}
