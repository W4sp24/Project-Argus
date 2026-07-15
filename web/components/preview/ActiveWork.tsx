"use client";

import Panel from "@/components/Panel";
import { FLAGS } from "@/lib/flags";

interface Pr {
  id: string;
  title: string;
  repo: string;
  status: "OPEN" | "REVIEW" | "DRAFT";
  age: string;
}

const MOCK_PRS: Pr[] = [
  { id: "#142", title: "feat(web): re-skin Research/Code/System modes", repo: "Project-Argus", status: "REVIEW", age: "2h" },
  { id: "#141", title: "fix: activity feed UTC normalization", repo: "Project-Argus", status: "OPEN", age: "1d" },
  { id: "#139", title: "chore: bump next.js patch release", repo: "Project-Argus", status: "DRAFT", age: "3d" },
];

const STATUS_CLASS: Record<Pr["status"], string> = {
  OPEN: "border-[var(--ac)] text-[var(--ac)]",
  REVIEW: "border-amber-400 text-amber-400",
  DRAFT: "border-ink-faint text-ink-faint",
};

/**
 * ACTIVE.WORK (§4 Code) [PREVIEW] — mock PR list (flags.activeWork). No real
 * GitHub connector exists in this branch's ancestry; this panel is static
 * demo data only, no `fetch(` (§8 grep guard).
 */
export default function ActiveWork() {
  return (
    <Panel label="ACTIVE.WORK" preview={FLAGS.activeWork === "preview"}>
      <ul className="divide-y divide-line">
        {MOCK_PRS.map((pr) => (
          <li key={pr.id} className="flex items-center gap-3 py-2">
            <span className="shrink-0 font-mono text-[11px] text-ink-faint">{pr.id}</span>
            <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{pr.title}</span>
            <span className={`shrink-0 border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] ${STATUS_CLASS[pr.status]}`}>
              {pr.status}
            </span>
            <span className="shrink-0 font-mono text-[10px] text-ink-faint">{pr.age}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
