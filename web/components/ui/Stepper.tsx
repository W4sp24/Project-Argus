"use client";

/**
 * The progress rail for multi-step dialogs (add-model, connect-integration).
 *
 * Two rules make it honest rather than decorative:
 *  - You may jump *back* to any step you have already cleared, never forward.
 *    `furthest` is the gate, so a step that is still unproven (an untested
 *    connection) cannot be skipped past by clicking its dot.
 *  - It is an ordered list with a real `aria-current="step"`, so the rail is
 *    navigable rather than four decorative circles a screen reader reads as
 *    unlabelled buttons.
 */
export default function Stepper({
  steps,
  current,
  furthest,
  onJump,
  className = "",
}: {
  steps: string[];
  /** Index of the step being shown. */
  current: number;
  /** Highest index reached so far — anything above this is not clickable. */
  furthest: number;
  onJump?: (index: number) => void;
  className?: string;
}) {
  return (
    <ol className={`flex items-center gap-1 ${className}`}>
      {steps.map((step, index) => {
        const active = index === current;
        const cleared = index < current;
        const reachable = index <= furthest;
        const label = `${index + 1} · ${step}`;

        return (
          <li key={step} className="flex min-w-0 flex-1 items-center gap-1">
            <button
              type="button"
              disabled={!reachable || active}
              aria-current={active ? "step" : undefined}
              aria-label={`Step ${label}`}
              onClick={() => reachable && !active && onJump?.(index)}
              className={`flex min-w-0 flex-1 flex-col gap-1.5 text-left transition-colors ${
                reachable && !active ? "cursor-pointer" : "cursor-default"
              } disabled:cursor-default`}
            >
              {/* The bar carries the state; the word underneath names it. */}
              <span
                aria-hidden
                className={`h-0.5 w-full transition-colors ${
                  active ? "bg-[var(--ac)]" : cleared ? "bg-[var(--ac)] opacity-45" : "bg-line"
                }`}
              />
              <span
                className={`truncate font-mono text-micro uppercase tracking-[0.14em] transition-colors ${
                  active
                    ? "text-[var(--ac)]"
                    : cleared
                      ? "text-ink-muted hover:text-ink"
                      : "text-ink-faint"
                }`}
              >
                <span className="tabular-nums">{index + 1}</span> {step}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
