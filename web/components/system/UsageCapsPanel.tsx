"use client";

import { useEffect, useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { ApiError, saveUsageCaps, useUsageCaps } from "@/lib/api";

/**
 * USAGE CAPS — self-configured soft caps behind the CLAUDE CODE panel's
 * 5H WINDOW / WEEKLY bars: `GET /api/usage/caps`, `PUT /api/usage/caps
 * {five_hour_cap, weekly_cap}` (422 on a non-positive value, surfaced as a
 * toast). Saving triggers `mutate()` so `useUsageCaps()` — and therefore the
 * CLAUDE CODE panel's bars — pick up the new value immediately.
 */
export default function UsageCapsPanel() {
  const { data: caps, isLoading, mutate } = useUsageCaps();
  const { show } = useToast();
  const [fiveHour, setFiveHour] = useState("");
  const [weekly, setWeekly] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!caps) return;
    setFiveHour(String(caps.five_hour_cap));
    setWeekly(caps.weekly_cap !== null ? String(caps.weekly_cap) : "");
  }, [caps]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;

    const fiveHourValue = Number(fiveHour);
    if (!Number.isFinite(fiveHourValue) || fiveHourValue <= 0) {
      show("usage caps :: 5-hour cap must be a positive number");
      return;
    }
    const trimmedWeekly = weekly.trim();
    let weeklyValue: number | null = null;
    if (trimmedWeekly !== "") {
      weeklyValue = Number(trimmedWeekly);
      if (!Number.isFinite(weeklyValue) || weeklyValue <= 0) {
        show("usage caps :: weekly cap must be a positive number, or blank for none");
        return;
      }
    }

    setBusy(true);
    try {
      await saveUsageCaps({ five_hour_cap: fiveHourValue, weekly_cap: weeklyValue });
      show("usage caps :: saved");
      mutate();
    } catch (error) {
      show(`usage caps :: ${error instanceof ApiError ? error.message : "save failed — backend offline?"}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel label="USAGE CAPS">
      <form onSubmit={save} className="flex flex-wrap items-end gap-3">
        <label className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="font-mono text-[9px] uppercase tracking-wide text-ink-faint">5-hour cap</span>
          <input
            type="number"
            min="1"
            value={fiveHour}
            onChange={(e) => setFiveHour(e.target.value)}
            placeholder={isLoading ? "loading…" : "e.g. 19000"}
            className="min-w-0 border border-line bg-sunken px-2.5 py-1.5 text-[12.5px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
          />
        </label>
        <label className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="font-mono text-[9px] uppercase tracking-wide text-ink-faint">weekly cap</span>
          <input
            type="number"
            min="1"
            value={weekly}
            onChange={(e) => setWeekly(e.target.value)}
            placeholder="not set"
            className="min-w-0 border border-line bg-sunken px-2.5 py-1.5 text-[12.5px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
          />
        </label>
        <button
          type="submit"
          disabled={!fiveHour.trim() || busy}
          className="shrink-0 border border-line px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
        >
          {busy ? "SAVING…" : "SAVE CAPS"}
        </button>
      </form>
      <p className="mt-3 font-mono text-[10px] text-ink-faint">
        self-configured estimates for the CLAUDE CODE panel&apos;s progress bars — not official Anthropic limits
      </p>
    </Panel>
  );
}
