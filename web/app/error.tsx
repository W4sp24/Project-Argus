"use client";

import Panel from "@/components/Panel";

/** Route-level error boundary: any page crash lands on a bordered panel, not a white screen. */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex min-h-[60vh] items-center justify-center p-6">
      <Panel label="ERROR" title="Something broke" className="max-w-md">
        <p className="text-sm text-ink-muted">
          {error.message || "An unexpected error occurred while rendering this page."}
        </p>
        {error.digest && (
          <p className="mt-2 font-mono text-[11px] text-ink-faint">digest: {error.digest}</p>
        )}
        <button
          onClick={reset}
          className="mt-4 border border-line bg-[var(--ac-bg)] px-4 py-2 font-mono text-[11px] uppercase tracking-[0.12em] text-[var(--ac)] transition-colors hover:border-lineHi"
        >
          Try again
        </button>
      </Panel>
    </div>
  );
}
