"use client";

import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";

interface MockCheck {
  name: string;
  status: "OK" | "WARN";
  detail: string;
}

// Mirrors backend/doctor.py's real check names/order (run_checks): vault,
// vault-git, database, chroma, keyring, then the two connectors.
const MOCK_CHECKS: MockCheck[] = [
  { name: "vault", status: "OK", detail: "vault path resolves" },
  { name: "vault-git", status: "OK", detail: "pre-apply snapshots ready" },
  { name: "database", status: "OK", detail: "all required tables present" },
  { name: "chroma", status: "WARN", detail: "chromadb not installed — pip install -e .[rag]" },
  { name: "keyring", status: "OK", detail: "OS keyring stores secrets" },
  { name: "gcal", status: "WARN", detail: "not connected — argus connect gcal" },
  { name: "todoist", status: "WARN", detail: "not connected — argus connect todoist <token>" },
];

const STATUS_CLASS: Record<string, string> = {
  OK: "border-ok text-ok",
  WARN: "border-amber-400 text-amber-400",
  FAIL: "border-danger text-danger",
};

/**
 * DOCTOR (§12) [PREVIEW] — `POST /api/doctor` isn't in this branch's
 * ancestry, so this mirrors `backend/doctor.py`'s real check names with mock
 * OK/WARN states. RUN AGAIN just toasts that the real endpoint is coming
 * with the backend branch — no fetch (§8 grep guard).
 */
export default function DoctorPanel() {
  const { show } = useToast();

  return (
    <Panel
      label="DOCTOR"
      preview
      headerRight={
        <button
          type="button"
          onClick={() => show("doctor :: arrives with the backend branch")}
          className="border border-line px-2 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-lineHi hover:text-ink"
        >
          RUN AGAIN
        </button>
      }
    >
      <div className="grid gap-2 sm:grid-cols-2">
        {MOCK_CHECKS.map((check) => (
          <div key={check.name} className="flex items-center gap-2 border border-line px-2.5 py-1.5">
            <span className={`shrink-0 border px-1.5 py-0.5 font-mono text-[9px] uppercase ${STATUS_CLASS[check.status]}`}>
              {check.status}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate font-mono text-[11.5px] text-ink">{check.name}</span>
              <span className="block truncate text-[10.5px] text-ink-faint">{check.detail}</span>
            </span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
