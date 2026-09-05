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

/** The four content modes, SYSTEM, and AUTOMATIONS — replaces per-page
 * theming (§2). AUTOMATIONS earns a mode of its own because the approved
 * design gives it one: its own tab and its own accent, so a screen driven by
 * a second service you have to keep alive is never mistaken for a native one
 * at a glance. */
export type Mode = "general" | "notebook" | "research" | "code" | "system" | "automations";

export const ACCENTS: Record<Mode, { ac: string; acBg: string }> = {
  general: { ac: "#a78bfa", acBg: "#171029" },
  // Study became Notebook; the cyan is unchanged on purpose -- the accent
  // is how the mode is recognised at a glance, and renaming it is not a
  // reason to make it look like a different place.
  notebook: { ac: "#22d3ee", acBg: "#0c1a20" },
  research: { ac: "#e879f9", acBg: "#210f20" },
  code: { ac: "#34d399", acBg: "#0b1712" },
  system: { ac: "#fbbf24", acBg: "#201804" },
  automations: { ac: "#60a5fa", acBg: "#0b1424" },
};

/** Where a mode tab's click navigates to. */
export const MODE_ROUTES: Record<Mode, string> = {
  general: "/dashboard",
  notebook: "/notebook",
  research: "/research",
  code: "/code",
  system: "/system",
  automations: "/automations",
};

const STORAGE_KEY = "argus-mode";

/**
 * Pathname is the single source of truth for the active mode: it's known
 * synchronously on both server and first client render (unlike localStorage),
 * so deep links and back/forward navigation always resolve the right accent
 * with zero flash. `/notebook*` catches the sub-pages (flashcards, exam, hub).
 */
function modeFromPathname(pathname: string): Mode {
  // Explicit rather than falling through. /sources is a GENERAL surface, and
  // saying so here is what gives the route a real active state instead of one
  // it lands on by accident. A seventh mode tab would be the wrong fix: `Mode`
  // is a closed union threaded through six places, and the two-letter tab
  // strip below `md` has no room for another.
  if (pathname.startsWith("/sources")) return "general";
  if (pathname.startsWith("/calendar")) return "general";
  if (pathname.startsWith("/notebook")) return "notebook";
  if (pathname.startsWith("/research")) return "research";
  if (pathname.startsWith("/code")) return "code";
  if (pathname.startsWith("/system")) return "system";
  if (pathname.startsWith("/automations")) return "automations";
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
