"use client";

/**
 * Manual override for the root scale.
 *
 * `globals.css` already sizes the UI from the reader's own browser/OS font
 * setting and steps it up on large displays. This is the escape hatch for when
 * that lands wrong — a 4K laptop panel at 150% Windows scaling, or someone who
 * simply wants more on screen. It writes `data-ui-scale` on <html>, which the
 * three rules in globals.css key off.
 */

export const UI_SCALES = ["compact", "default", "large"] as const;
export type UiScale = (typeof UI_SCALES)[number];

export const UI_SCALE_KEY = "argus-ui-scale";

function isScale(value: string | null): value is UiScale {
  return value !== null && (UI_SCALES as readonly string[]).includes(value);
}

export function readUiScale(): UiScale {
  try {
    const stored = window.localStorage.getItem(UI_SCALE_KEY);
    return isScale(stored) ? stored : "default";
  } catch {
    return "default";
  }
}

export function applyUiScale(scale: UiScale): void {
  const root = document.documentElement;
  // "default" means "whatever the width steps and the OS say", so the
  // attribute is removed rather than set to a value that would override them.
  if (scale === "default") delete root.dataset.uiScale;
  else root.dataset.uiScale = scale;

  try {
    window.localStorage.setItem(UI_SCALE_KEY, scale);
  } catch {
    // best-effort persistence (private browsing etc.) — the DOM is already set
  }
}

/**
 * Runs before first paint, inlined into the document by the root layout, so
 * the app never renders at one size and then jumps to another.
 */
export const UI_SCALE_BOOT_SCRIPT = `try{var s=localStorage.getItem(${JSON.stringify(
  UI_SCALE_KEY,
)});if(s==="compact"||s==="large"){document.documentElement.dataset.uiScale=s}}catch(e){}`;
