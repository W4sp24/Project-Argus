import Panel from "@/components/Panel";

interface Step {
  label: string;
  command: string;
  state: "OK" | "OPTIONAL";
}

// Mirrors the README quickstart section verbatim, in order.
const STEPS: Step[] = [
  { label: "install backend deps", command: 'pip install -e ".[dev]"', state: "OK" },
  { label: "create or point at a vault", command: "argus init ./my-vault", state: "OK" },
  { label: "build the dashboard once", command: "cd web && npm install", state: "OK" },
  { label: "run — dashboard :3000, api :8000", command: "argus web", state: "OK" },
  { label: "verify the install", command: "argus doctor", state: "OK" },
  { label: "chat/RAG extras (optional)", command: 'pip install -e ".[rag]"', state: "OPTIONAL" },
  { label: "google calendar (optional)", command: "argus connect gcal", state: "OPTIONAL" },
  { label: "todoist (optional)", command: "argus connect todoist <token>", state: "OPTIONAL" },
];

/**
 * SETUP.GUIDE (§12) — checklist mirroring the README quickstart. Completion
 * states are a static placeholder (OK for the steps this running instance
 * necessarily satisfied, OPTIONAL for connectors); `POST /api/doctor` isn't
 * in this branch's ancestry yet, so real per-step health isn't wired.
 */
export default function SetupGuide() {
  return (
    <Panel label="SETUP.GUIDE">
      <ul className="space-y-2">
        {STEPS.map((step) => (
          <li key={step.command} className="flex flex-wrap items-center gap-2.5 border-b border-line pb-2 last:border-b-0 last:pb-0">
            <span
              className={`shrink-0 font-mono text-[9px] uppercase tracking-[0.1em] ${
                step.state === "OK" ? "text-ok" : "text-ink-faint"
              }`}
            >
              {step.state === "OK" ? "✓ ok" : "○ optional"}
            </span>
            <span className="min-w-0 flex-1 text-[13px] text-ink-muted">{step.label}</span>
            <code className="shrink-0 border border-line bg-sunken px-2 py-0.5 font-mono text-[11px] text-ink">
              {step.command}
            </code>
          </li>
        ))}
      </ul>
      <p className="mt-3 font-mono text-[10px] text-ink-faint">
        completion states are static — once `POST /api/doctor` lands, these derive from real checks
      </p>
    </Panel>
  );
}
