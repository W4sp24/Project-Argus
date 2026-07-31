"use client";

import { useEffect } from "react";
import { applyUiScale, readUiScale } from "@/lib/uiScale";

/**
 * Re-applies the saved interface size once React has finished with the DOM.
 *
 * `UI_SCALE_BOOT_SCRIPT` sets `data-ui-scale` on <html> before first paint,
 * which is right for avoiding a flash of the wrong size but is not durable:
 * `data-ui-scale` is not an attribute React rendered, so when hydration fails
 * and React falls back to client rendering ("the entire root will switch to
 * client rendering", React error #423) the rebuilt root has no such attribute
 * and the setting silently reverts to Default.
 *
 * /dashboard does exactly that on every load, so anyone who chose Compact or
 * Large saw it apply for a moment and then vanish — on the one page they look
 * at most. Verified in the browser: the attribute reads "large" immediately
 * after load and null two seconds later, while /system (which hydrates
 * cleanly) keeps it.
 *
 * An effect runs after commit, so this restores the attribute after any such
 * rebuild, and it does not depend on which mismatch caused it — the dashboard
 * still logs React #418/#423 and that is worth fixing separately, but the
 * user's setting no longer rides on hydration succeeding.
 */
export default function UiScaleSync() {
  useEffect(() => {
    applyUiScale(readUiScale());
  }, []);
  return null;
}
