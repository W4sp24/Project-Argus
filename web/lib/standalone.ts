"use client";

import { useEffect, useState } from "react";

/**
 * Is this window a popped-out mode, and which one?
 *
 * The flag arrives once, on the opening URL, and is then kept in
 * **sessionStorage** rather than localStorage. That choice is the whole
 * mechanism: sessionStorage is scoped to a single window, so the flag survives
 * client-side navigation and reload *inside* the popped-out window and cannot
 * leak into the main one. localStorage would make every window believe it was
 * standalone the moment one of them was.
 */

const KEY = "argus-standalone";

/** Values this app understands. Anything else came from a hand-typed URL. */
const KNOWN = new Set(["standalone"]);

/**
 * Pure, so it can be tested without a window.
 *
 * The URL wins over the stored value rather than merging with it: a window
 * reused for a different purpose must not inherit the previous one's mode.
 */
export function resolveStandalone(search: string, stored: string | null): string | null {
  const fromUrl = new URLSearchParams(search).get("window");
  const value = fromUrl ?? stored;
  return value !== null && KNOWN.has(value) ? value : null;
}

export function useStandalone(): string | null {
  // Starts null so the server and the first client render agree. A popped-out
  // window therefore paints ordinary chrome for one frame, which is the right
  // trade: a hydration mismatch is worse than a frame of the wrong top bar.
  const [value, setValue] = useState<string | null>(null);

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = window.sessionStorage.getItem(KEY);
    } catch {
      // sessionStorage throws in some embedded and private contexts. The
      // window then simply renders as an ordinary one.
    }
    const resolved = resolveStandalone(window.location.search, stored);
    if (resolved !== null) {
      try {
        window.sessionStorage.setItem(KEY, resolved);
      } catch {
        // Best-effort: without it the flag is lost on the next navigation,
        // which degrades to ordinary chrome rather than breaking anything.
      }
    }
    setValue(resolved);
  }, []);

  return value;
}
