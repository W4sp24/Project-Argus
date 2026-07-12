"use client";

import GlassCard from "@/components/GlassCard";

/** Route-level error boundary: any page crash lands on a glass card, not a white screen. */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex min-h-[60vh] items-center justify-center p-6">
      <GlassCard label="ERROR" title="Something broke" className="max-w-md">
        <p className="text-sm text-ink-muted">
          {error.message || "An unexpected error occurred while rendering this page."}
        </p>
        {error.digest && (
          <p className="mt-2 font-mono text-[11px] text-ink-faint">digest: {error.digest}</p>
        )}
        <button
          onClick={reset}
          className="mt-4 rounded-xl bg-gradient-to-r from-primary to-accent px-4 py-2 font-display text-sm text-white"
        >
          Try again
        </button>
      </GlassCard>
    </div>
  );
}
