"use client";

import { useEffect, useState } from "react";
import type { ChatMessage, ToolStep } from "@/lib/chat";

interface ToolTraceProps {
  steps: ToolStep[];
  status: ChatMessage["status"];
  /** The turn's own start/end (`Date.now()` ms) — present on a live turn.
   *  Absent on a thread restored from `chat_messages`, where every step is
   *  already finished and there is nothing left to time live; `spanOf`
   *  derives the duration from the steps themselves in that case. */
  startedAt?: number;
  endedAt?: number;
}

/** Tool names the agent actually dispatches (`build_vault_tools`,
 *  backend/agent/runtime.py's `_summarize_*`). `run_automation_<slug>` is
 *  matched by prefix since the slug is per-workflow and unbounded. */
function verbFor(name: string | undefined): string {
  if (!name) return "Thinking…";
  if (name === "search_vault") return "Searching your vault…";
  if (name === "read_note") return "Reading a note…";
  if (name === "list_tasks") return "Checking your tasks…";
  if (name.startsWith("run_automation_")) return "Running an automation…";
  // An unrecognised name (a tool added after this was written) would
  // otherwise show as a raw snake_case identifier — split it instead so the
  // trace still reads as a sentence rather than a debug dump.
  return `${humanize(name)}…`;
}

/** `search_vault` -> `Search vault`. Backs the fallback verb above and the
 *  tool-name column in the expanded step list, where `label` from the wire
 *  frame is no help — the backend sets it to the tool's own name (see
 *  `_summarize_search_vault` et al.), not a human sentence. */
function humanize(name: string): string {
  const words = name.split("_").filter(Boolean);
  if (words.length === 0) return "Tool";
  return words.map((word, i) => (i === 0 ? word[0].toUpperCase() + word.slice(1) : word)).join(" ");
}

function seconds(ms: number): string {
  return `${(Math.max(ms, 0) / 1000).toFixed(1)}s`;
}

/** Duration for the collapsed summary. A live turn has its own start/end;
 *  a restored thread has neither (see `ToolTraceProps.startedAt`), so this
 *  falls back to the span between the first step's start and the last
 *  step's end — safe because every step there is already finished. */
function spanOf(steps: ToolStep[], startedAt?: number, endedAt?: number): number | null {
  if (startedAt !== undefined && endedAt !== undefined) return endedAt - startedAt;
  if (steps.length === 0) return null;
  const starts = steps.map((step) => step.startedAt);
  const ends = steps.map((step) => step.endedAt ?? step.startedAt);
  return Math.max(...ends) - Math.min(...starts);
}

/** ok / failed / interrupted glyph for one row of the expanded list.
 *  `ok` is only ever undefined when the turn was stopped or hit an error
 *  mid-call — the `end` frame that would have set it never arrived — so
 *  that third state gets its own (warn, not danger) glyph rather than being
 *  folded into "failed". */
function StepGlyph({ ok }: { ok?: boolean }) {
  if (ok === true) {
    return (
      <span aria-hidden="true" className="text-ok">
        ✓
      </span>
    );
  }
  if (ok === false) {
    return (
      <span aria-hidden="true" className="text-danger">
        ✕
      </span>
    );
  }
  return (
    <span aria-hidden="true" className="text-warn">
      …
    </span>
  );
}

function PathLine({ path, indent }: { path: string; indent: string }) {
  return (
    <p className={`mt-0.5 flex gap-1.5 text-meta text-ink-faint ${indent}`}>
      <span aria-hidden="true">⌗</span>
      <span className="truncate">{path}</span>
    </p>
  );
}

/**
 * The agent's "what am I doing" surface. One component, two states:
 *
 * - while `status === "streaming"`, a single live line with an elapsed
 *   timer and the latest step's findings underneath;
 * - once the turn settles, a collapsed `N steps · Ts` summary that expands
 *   into the full per-step list.
 *
 * Doubles as the replay view for a thread loaded from `chat_messages` —
 * same props, just `status` already settled and `startedAt` absent.
 */
export default function ToolTrace({ steps, status, startedAt, endedAt }: ToolTraceProps) {
  const streaming = status === "streaming";
  const [expanded, setExpanded] = useState(false);

  // The one interval this component is allowed to own (perf budget: at most
  // one timer interval at a time). It exists only while a turn is actually
  // streaming and is torn down the moment that stops being true, whether by
  // unmount or by `status` moving on — never left running past the turn.
  //
  // A typed-verb effect via useTypewriter was considered and dropped: that
  // hook opens its own setInterval, and the verb changes exactly when this
  // ticker is already running (mid-stream, as steps start and end), which
  // would put two intervals live at once. Static text keeps the guarantee
  // simple instead of "usually one."
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!streaming) return;
    const id = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(id);
  }, [streaming]);

  if (streaming) {
    // At most one step is running at a time — the agent awaits each tool
    // call before starting the next — so "the running step" is just the
    // last one without an `end` frame yet. None running (between calls, or
    // before the first) reads as "Thinking…" rather than a blank line.
    const running = [...steps].reverse().find((step) => step.endedAt === undefined);
    const verb = verbFor(running?.name);
    const latest = steps[steps.length - 1];
    const elapsed = startedAt !== undefined ? now - startedAt : 0;

    return (
      <div
        className="border border-line bg-panel px-3.5 py-2.5 font-mono text-label text-ink-muted"
        aria-live="polite"
      >
        <div className="flex items-baseline gap-2">
          <span aria-hidden="true" className="shrink-0 text-[var(--ac)]">
            ▍
          </span>
          <span className="min-w-0 flex-1 truncate">
            {verb}
            {/* The one perpetual animation this component is allowed (§ design
                constraints) — a CSS keyframe, not a timer, so it doesn't count
                against the interval budget above. */}
            <span aria-hidden="true" className="animate-blink pl-0.5 text-[var(--ac)]">
              ▍
            </span>
          </span>
          {/* Outside the live region's announcements on purpose: this ticks
              ten times a second, and a screen reader reading each tick would
              bury the verb and findings the region exists to convey. */}
          <span
            aria-hidden="true"
            className="shrink-0 tabular-nums text-meta text-ink-faint"
          >
            {seconds(elapsed)}
          </span>
        </div>
        {latest?.detail && (
          <p className="mt-1 truncate pl-4 text-meta text-ink-faint">{latest.detail}</p>
        )}
        {latest?.paths?.map((path) => <PathLine key={path} path={path} indent="pl-4" />)}
      </div>
    );
  }

  // A turn that never touched a tool leaves nothing worth a panel. That holds
  // for a failed or stopped turn too: "0 steps · 2.1s" sitting above an error
  // message is chrome, not information, and the message already says what
  // happened.
  if (steps.length === 0) return null;

  const span = spanOf(steps, startedAt, endedAt);

  return (
    <div className="border border-line bg-panel font-mono text-label text-ink-muted">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center gap-2 px-3.5 py-2 text-left hover:text-ink-bright"
      >
        <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
        <span>
          {steps.length} step{steps.length === 1 ? "" : "s"}
          {span !== null ? ` · ${seconds(span)}` : ""}
        </span>
      </button>
      {expanded && (
        <ul className="space-y-2 border-t border-line px-3.5 py-2.5">
          {steps.map((step) => (
            <li key={step.callId}>
              <div className="flex items-center gap-1.5">
                <StepGlyph ok={step.ok} />
                <span>{humanize(step.name)}</span>
              </div>
              {step.detail && <p className="pl-5 text-meta text-ink-faint">{step.detail}</p>}
              {step.paths?.map((path) => <PathLine key={path} path={path} indent="pl-5" />)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
