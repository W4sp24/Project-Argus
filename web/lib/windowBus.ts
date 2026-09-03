"use client";

import { useEffect, useState } from "react";

/**
 * Which Argus windows are open, so a popped-out mode *moves* instead of being
 * shown twice.
 *
 * `BroadcastChannel` is a standard API available in both the browser and the
 * Electron renderer, and it is same-origin by definition — which is the whole
 * security story here, since nothing else can join the channel.
 *
 * Every consumer degrades to "no other window" when it is unavailable, so
 * nothing in the app depends on this existing. That matters: it is a
 * convenience, and a convenience that can break navigation is not one.
 */

const CHANNEL = "argus-windows";

function open(): BroadcastChannel | null {
  try {
    return new BroadcastChannel(CHANNEL);
  } catch {
    return null;
  }
}

/**
 * Called by the standalone window: announce presence now, and absence on
 * close. Returns its own cleanup.
 */
export function announceStandalone(): () => void {
  const bus = open();
  if (bus === null) return () => {};

  const say = (present: boolean) => bus.postMessage({ notebook: present });
  say(true);
  // Answer late joiners. A main window opened *after* the pop-out would
  // otherwise never learn it exists, and would offer to open a second one.
  bus.onmessage = (event) => {
    if (event.data?.ask === "notebook") say(true);
  };

  const bye = () => say(false);
  window.addEventListener("beforeunload", bye);
  return () => {
    bye();
    window.removeEventListener("beforeunload", bye);
    bus.close();
  };
}

/** Called by the main window. False whenever the channel is unavailable. */
export function useNotebookWindowOpen(): boolean {
  const [present, setPresent] = useState(false);

  useEffect(() => {
    const bus = open();
    if (bus === null) return;
    bus.onmessage = (event) => {
      if (typeof event.data?.notebook === "boolean") setPresent(event.data.notebook);
    };
    // Ask, in case the pop-out was already open before this window loaded.
    bus.postMessage({ ask: "notebook" });
    return () => bus.close();
  }, []);

  return present;
}
