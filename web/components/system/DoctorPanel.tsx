"use client";

import Panel from "@/components/Panel";
import Button from "@/components/ui/Button";
import { useDoctor } from "@/lib/api";

const STATUS_CLASS: Record<string, string> = {
  OK: "border-ok text-ok",
  WARN: "border-warn text-warn",
  FAIL: "border-danger text-danger",
};

/**
 * DOCTOR (§12) — wired to the real `POST /api/doctor` (backend/doctor.py).
 * Lives outside `components/preview/` now that it fetches (§8 grep guard:
 * no `fetch(` inside preview/**). RUN AGAIN revalidates the shared
 * `useDoctor()` SWR cache, so SETUP.GUIDE's derived states update too.
 */
export default function DoctorPanel() {
  const { data: checks, isLoading, mutate } = useDoctor();

  return (
    <Panel
      label="DOCTOR"
      headerRight={
        <Button variant="quiet" onClick={() => mutate()} disabled={isLoading}>
          {isLoading ? "RUNNING…" : "RUN AGAIN"}
        </Button>
      }
    >
      {isLoading && !checks && <p className="text-label text-ink-faint">running checks…</p>}
      {!isLoading && !checks && (
        <p className="text-label text-ink-faint">
          couldn&apos;t reach the backend — is <span className="font-mono text-xs">argus web</span> running?
        </p>
      )}
      {checks && (
        <div className="grid gap-2 sm:grid-cols-2">
          {checks.map((check) => (
            <div key={check.name} className="flex items-center gap-2 border border-line px-2.5 py-1.5">
              <span
                className={`shrink-0 border px-1.5 py-0.5 font-mono text-micro uppercase ${STATUS_CLASS[check.status]}`}
              >
                {check.status}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-label text-ink">{check.name}</span>
                <span className="block truncate text-meta text-ink-faint">{check.detail}</span>
              </span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
