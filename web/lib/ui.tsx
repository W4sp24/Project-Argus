"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

/**
 * Shared shell-UI state (Phase F): one provider owns which overlay surface is
 * open (chat drawer, command palette, note modal) so the TopBar controls and
 * the palette actions can drive the same instances. The focus timer keeps its
 * ticking state local to FocusTimer (§10: interval only while running, and a
 * 1 Hz context update would re-render every consumer) — it registers a
 * `start` callback here so the palette can kick it off.
 */
interface UiState {
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
  toggleDrawer: () => void;
  noteOpen: boolean;
  setNoteOpen: (open: boolean) => void;
  paletteOpen: boolean;
  setPaletteOpen: (open: boolean) => void;
  /** FocusTimer registers its start function on mount (null on unmount). */
  registerFocusStart: (fn: (() => void) | null) => void;
  /** Start a focus session if a FocusTimer is mounted; no-op otherwise. */
  startFocus: () => void;
}

const UiContext = createContext<UiState | null>(null);

export function useUi(): UiState {
  const state = useContext(UiContext);
  if (!state) throw new Error("useUi must be used inside <UiProvider>");
  return state;
}

export function UiProvider({ children }: { children: ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const focusStart = useRef<(() => void) | null>(null);

  const toggleDrawer = useCallback(() => setDrawerOpen((open) => !open), []);
  const registerFocusStart = useCallback((fn: (() => void) | null) => {
    focusStart.current = fn;
  }, []);
  const startFocus = useCallback(() => focusStart.current?.(), []);

  const value = useMemo(
    () => ({
      drawerOpen,
      setDrawerOpen,
      toggleDrawer,
      noteOpen,
      setNoteOpen,
      paletteOpen,
      setPaletteOpen,
      registerFocusStart,
      startFocus,
    }),
    [drawerOpen, noteOpen, paletteOpen, toggleDrawer, registerFocusStart, startFocus],
  );

  return <UiContext.Provider value={value}>{children}</UiContext.Provider>;
}
