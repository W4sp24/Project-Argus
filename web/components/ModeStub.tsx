"use client";

import Panel from "@/components/Panel";
import { useMode } from "@/lib/mode";

function formatToday(): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

/**
 * Minimal §4 shared skeleton for modes that don't have their real screen yet
 * (Phase E fills these in): status line + one Panel. Reads the active mode
 * from context rather than a prop so each stub route is a one-liner.
 */
export default function ModeStub() {
  const { mode } = useMode();
  const label = mode.toUpperCase();

  return (
    <>
      <header className="mb-8 animate-rise">
        <p className="eyebrow mb-2">{`// SYS.${label} :: ${formatToday()}`}</p>
        <h1 className="font-display text-3xl font-semibold tracking-tight md:text-4xl">
          {label}
          <span className="text-[var(--ac)]">_</span>
        </h1>
      </header>
      <Panel label={label} title={`${label} mode is coming online`}>
        <p className="text-sm text-ink-muted">
          The mode tab, accent, and route are wired up now — panels and real data land in
          a later phase.
        </p>
      </Panel>
    </>
  );
}
