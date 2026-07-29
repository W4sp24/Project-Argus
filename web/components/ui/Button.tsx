"use client";

import { forwardRef, type ButtonHTMLAttributes } from "react";

/**
 * The shared button. Before this, buttons were ad-hoc class strings using 8
 * different padding pairs, 7 font sizes and 5 tracking values — near-identical
 * controls that had quietly drifted apart, and hit targets as small as ~24px.
 *
 * Two deliberate choices:
 *  - No `focus:outline-none` anywhere. Tailwind emits that at specificity
 *    (0,2,0), which outranks the bare `:focus-visible` rule in globals.css —
 *    that is how the command palette input ended up with no focus ring at all.
 *  - `type="button"` by default. Several buttons in the app omit it, which is
 *    harmless until the day one of them lands inside a <form>.
 */

export type ButtonVariant = "primary" | "secondary" | "quiet" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

const VARIANTS: Record<ButtonVariant, string> = {
  /** The one action a surface is really for. */
  primary: "border border-line bg-[var(--ac-bg)] text-[var(--ac)] hover:border-lineHi",
  /** Everything else with equal weight — the app's dominant button. */
  secondary: "border border-line text-ink hover:border-lineHi",
  /** Panel-header actions: present, but not competing with the content. */
  quiet: "border border-line text-ink-faint hover:border-lineHi hover:text-ink",
  /** Borderless row actions (the hover-revealed `×`). */
  ghost: "text-ink-faint hover:text-ink",
  danger: "border border-danger text-danger hover:bg-[rgba(251,113,133,0.08)]",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "min-h-8 px-2.5 py-1 text-meta", // 32px tall
  md: "min-h-9 px-3.5 py-1.5 text-label", // 36px tall
};

const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
}>(function Button(
  { variant = "secondary", size = "sm", className = "", type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={`inline-flex items-center justify-center gap-1.5 font-mono uppercase tracking-[0.12em] transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...rest}
    />
  );
});

export default Button;
