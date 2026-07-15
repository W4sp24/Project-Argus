"use client";

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  type CSSProperties,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { useToast } from "@/components/Toast";

/** The four content modes plus SYSTEM — replaces per-page theming (§2). */
export type Mode = "general" | "study" | "research" | "code" | "system";

export const ACCENTS: Record<Mode, { ac: string; acBg: string }> = {
  general: { ac: "#a78bfa", acBg: "#171029" },
  study: { ac: "#22d3ee", acBg: "#0c1a20" },
  research: { ac: "#e879f9", acBg: "#210f20" },
  code: { ac: "#34d399", acBg: "#0b1712" },
  system: { ac: "#fbbf24", acBg: "#201804" },
};

/** Where a mode tab's click navigates to. */
export const MODE_ROUTES: Record<Mode, string> = {
  general: "/dashboard",
  study: "/study",
  research: "/research",
  code: "/code",
  system: "/system",
};

const STORAGE_KEY = "argus-mode";

/**
 * Pathname is the single source of truth for the active mode: it's known
 * synchronously on both server and first client render (unlike localStorage),
 * so deep links and back/forward navigation always resolve the right accent
 * with zero flash. `/study*` catches the sub-pages (flashcards, exam, hub).
 */
function modeFromPathname(pathname: string): Mode {
  if (pathname.startsWith("/study")) return "study";
  if (pathname.startsWith("/research")) return "research";
  if (pathname.startsWith("/code")) return "code";
  if (pathname.startsWith("/system")) return "system";
  return "general";
}

interface ModeState {
  mode: Mode;
  /** Navigates to the mode's route; context updates reactively from the new pathname. */
  setMode: (mode: Mode) => void;
}

const ModeContext = createContext<ModeState | null>(null);

export function useMode(): ModeState {
  const state = useContext(ModeContext);
  if (!state) throw new Error("useMode must be used inside <ModeProvider>");
  return state;
}

/**
 * ModeProvider (§2): derives the active mode from the route, sets `--ac`/
 * `--ac-bg` inline on a single wrapper div (one style recalc per switch —
 * consumers read the CSS vars, mode is never threaded through props),
 * persists the resolved mode to localStorage for cross-session continuity
 * (write-only — reading it back would race the pathname-derived mode and
 * risk a hydration mismatch, so routing always wins), and toasts
 * `mode :: {MODE} loaded` on every mode CHANGE (never on the initial load).
 */
export function ModeProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { show } = useToast();

  const mode = modeFromPathname(pathname ?? "/dashboard");
  const previousMode = useRef<Mode | null>(null);

  useEffect(() => {
    if (previousMode.current !== null && previousMode.current !== mode) {
      show(`mode :: ${mode.toUpperCase()} loaded`);
    }
    previousMode.current = mode;
    try {
      window.localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // localStorage can throw in private-browsing/embedded contexts — persistence is best-effort.
    }
  }, [mode, show]);

  function setMode(next: Mode) {
    router.push(MODE_ROUTES[next]);
  }

  const accent = ACCENTS[mode];
  const style = { "--ac": accent.ac, "--ac-bg": accent.acBg } as CSSProperties;

  return (
    <ModeContext.Provider value={{ mode, setMode }}>
      <div style={style}>{children}</div>
    </ModeContext.Provider>
  );
}
