"use client";

import Panel from "@/components/Panel";
import { useDoctor } from "@/lib/api";

interface Step {
  label: string;
  command: string;
  /** Name of the matching `argus doctor` check (backend/doctor.py), when one
   * exists. Omitted for steps a running instance necessarily already
   * satisfied (nothing in `run_checks` verifies "did npm install run"). */
  check?: string;
  state: "OK" | "OPTIONAL";
}

// Mirrors the README quickstart section verbatim, in order.
const STEPS: Step[] = [
  { label: "install backend deps", command: 'pip install -e ".[dev]"', state: "OK" },
  { label: "create or point at a vault", command: "argus init ./my-vault", check: "vault", state: "OK" },
  { label: "build the dashboard once", command: "cd web && npm install", state: "OK" },
  { label: "run — dashboard :3000, api :8000", command: "argus web", state: "OK" },
  { label: "verify the install", command: "argus doctor", state: "OK" },
  {
    label: "chat/RAG extras (optional)",
    command: 'pip install -e ".[rag]"',
    check: "chroma",
    state: "OPTIONAL",
  },
  { label: "google calendar (optional)", command: "argus connect gcal", check: "gcal", state: "OPTIONAL" },
  {
    label: "todoist (optional)",
    command: "argus connect todoist <token>",
    check: "todoist",
    state: "OPTIONAL",
  },
];

/**
 * SETUP.GUIDE (§12) — checklist mirroring the README quickstart. Completion
 * states derive from the real `POST /api/doctor` results (shared `useDoctor()`
 * SWR cache with DoctorPanel) where a matching check exists; the steps a
 * running instance necessarily already satisfied (backend deps installed,
 * dashboard built, `argus web` running) stay statically OK — `argus doctor`
 * has no check for "did npm install happen".
 */
export default function SetupGuide() {
  const { data: checks } = useDoctor();
  const byName = new Map((checks ?? []).map((check) => [check.name, check]));

  function resolve(step: Step): { ok: boolean; text: string } {
    if (!step.check) return { ok: step.state === "OK", text: step.state === "OK" ? "✓ ok" : "○ optional" };
    const found = byName.get(step.check);
    if (!found) return { ok: step.state === "OK", text: step.state === "OK" ? "✓ ok" : "○ optional" };
    if (found.status === "OK") return { ok: true, text: "✓ ok" };
    return step.state === "OK"
      ? { ok: false, text: `✗ ${found.status.toLowerCase()}` }
      : { ok: false, text: "○ optional" };
  }

  return (
    <Panel label="SETUP.GUIDE">
      <ul className="space-y-2">
        {STEPS.map((step) => {
          const { ok, text } =
            step.label === "verify the install"
              ? { ok: Boolean(checks), text: checks ? "✓ ok" : "○ pending" }
              : resolve(step);
          return (
            <li
              key={step.command}
              className="flex flex-wrap items-center gap-2.5 border-b border-line pb-2 last:border-b-0 last:pb-0"
            >
              <span
                className={`shrink-0 font-mono text-[9px] uppercase tracking-[0.1em] ${
                  ok ? "text-ok" : "text-ink-faint"
                }`}
              >
                {text}
              </span>
              <span className="min-w-0 flex-1 text-[13px] text-ink-muted">{step.label}</span>
              <code className="shrink-0 border border-line bg-sunken px-2 py-0.5 font-mono text-[11px] text-ink">
                {step.command}
              </code>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 font-mono text-[10px] text-ink-faint">
        completion states derive from `POST /api/doctor` where a matching check exists
      </p>
    </Panel>
  );
}
