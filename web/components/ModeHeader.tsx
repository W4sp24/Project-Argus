"use client";

import { useTypewriter } from "@/lib/useTypewriter";

function formatToday(): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

interface ModeHeaderProps {
  mode: string;
  /** Short typed line (§5 typed greeting) — re-types on every mount, i.e. every mode switch. */
  greeting: string;
  /** Overrides the trailing status segment; defaults to the shared health line. */
  status?: string;
}

/**
 * Shared §4 mode-page opener: status line + typed greeting with a blinking
 * cursor. Used by Research/Code/System (each a fresh mount on mode switch,
 * so the typewriter naturally re-runs — §5). `/dashboard` keeps its own
 * bespoke header (out of scope here — another agent owns that route).
 */
export default function ModeHeader({ mode, greeting, status }: ModeHeaderProps) {
  const { output, done } = useTypewriter(greeting);
  const statusLine = status ?? "vault OK · index OK · agent idle";

  return (
    <header className="mb-8 animate-rise">
      <p className="eyebrow mb-2">{`// SYS.${mode.toUpperCase()} :: ${formatToday()} :: ${statusLine}`}</p>
      <h1 className="font-mono text-[23px] font-semibold tracking-tight text-ink-bright">
        {output}
        <span className={`text-[var(--ac)] ${done ? "animate-blink" : ""}`}>▊</span>
      </h1>
    </header>
  );
}
