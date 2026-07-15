"use client";

import { useEffect, useState } from "react";

/**
 * Terminal typing effect (§5): one interval + slice into state.
 * - clears the interval on unmount and whenever `text` changes
 * - renders the full text immediately under prefers-reduced-motion
 * At most one typewriter interval runs per mounted consumer (§10 budget:
 * callers must not mount more than one typing element at a time).
 */
export function useTypewriter(
  text: string,
  speedMs = 34,
): { output: string; done: boolean } {
  const [output, setOutput] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!text) {
      setOutput("");
      setDone(true);
      return;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setOutput(text);
      setDone(true);
      return;
    }
    setOutput("");
    setDone(false);
    let i = 0;
    const id = window.setInterval(() => {
      i += 1;
      setOutput(text.slice(0, i));
      if (i >= text.length) {
        window.clearInterval(id);
        setDone(true);
      }
    }, speedMs);
    return () => window.clearInterval(id);
  }, [text, speedMs]);

  return { output, done };
}
