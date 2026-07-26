"use client";

import { useEffect, useState } from "react";
import Panel from "@/components/Panel";
import Button from "@/components/ui/Button";
import { applyUiScale, readUiScale, UI_SCALES, type UiScale } from "@/lib/uiScale";

const BLURB: Record<UiScale, string> = {
  compact: "more on screen, smaller text",
  default: "follows your browser and screen size",
  large: "bigger text everywhere",
};

/**
 * DISPLAY (§12) — the size of the whole interface.
 *
 * Everything is sized in rem off a single root percentage, so one setting
 * moves type, padding, gaps and the rail together rather than just zooming
 * the fonts out of proportion with their containers.
 */
export default function DisplayPanel() {
  // Read after mount: localStorage does not exist during SSR, and rendering a
  // guessed selection first would flash the wrong one.
  const [scale, setScale] = useState<UiScale | null>(null);
  useEffect(() => setScale(readUiScale()), []);

  function choose(next: UiScale) {
    applyUiScale(next);
    setScale(next);
  }

  return (
    <Panel label="DISPLAY">
      <div role="radiogroup" aria-label="Interface size" className="grid gap-2 sm:grid-cols-3">
        {UI_SCALES.map((option) => {
          const active = scale === option;
          return (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => choose(option)}
              className={`border p-3 text-left transition-colors ${
                active ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
              }`}
            >
              <span className="block font-mono text-label uppercase tracking-[0.12em] text-ink">
                {option}
              </span>
              <span className="mt-1 block text-meta text-ink-muted">{BLURB[option]}</span>
            </button>
          );
        })}
      </div>

      <p className="mt-3 border-t border-line pt-2 text-meta leading-relaxed text-ink-faint">
        Argus sizes itself from your browser&apos;s own font setting and grows on large displays.
        Use this if that lands wrong. Your browser&apos;s zoom still works on top of it.
      </p>

      <div className="mt-3">
        <Button variant="quiet" onClick={() => choose("default")} disabled={scale === "default"}>
          RESET
        </Button>
      </div>
    </Panel>
  );
}
