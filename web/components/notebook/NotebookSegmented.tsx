"use client";

/**
 * The Notebook's segmented switcher — equal-width pills, sentence case.
 *
 * A separate component rather than a variant of `components/ui/SegmentedControl`
 * on purpose. That one is shared with /automations, AgentUsage and TokenUsage,
 * where its mono-uppercase chrome is correct; giving it a second skin would put
 * four unrelated screens one prop away from changing. This one is a different
 * shape as well as a different colour — the artboards draw these as full-width
 * pill rows rather than a compact chrome switcher — so there was no single
 * component to share anyway.
 *
 * No `focus:outline-none` — see `components/ui/Button.tsx` for why that class is
 * banned on interactive elements in this repo.
 */
export default function NotebookSegmented<T extends string>({
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
    <div className="flex gap-2">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onChange(option)}
          aria-pressed={value === option}
          className={`flex-1 rounded-ctl border px-3 py-2.5 text-center text-ctl transition-colors ${
            value === option
              ? "border-[var(--ac)] bg-nb-acBg font-semibold text-[var(--ac)]"
              : "border-nb-line bg-nb-raised text-nb-ink hover:border-nb-lineHi"
          }`}
        >
          {labels[option]}
        </button>
      ))}
    </div>
  );
}
