"use client";

import { useId, type ReactNode } from "react";

/**
 * Labelled form control. The app had no inline validation anywhere — a repo
 * grep for `aria-invalid` and `aria-describedby` returned zero hits, and most
 * inputs were placeholder-only, so the label vanished the moment you typed.
 *
 * Uses a render prop rather than cloneElement: the caller keeps full control of
 * its own input/textarea/select (and its typing), and gets back exactly the
 * wiring it must not forget.
 */

export interface FieldControlProps {
  id: string;
  "aria-describedby": string | undefined;
  "aria-invalid": boolean | undefined;
}

/** Shared control styling. No `focus:outline-none` — see ui/Button. */
export const FIELD_CONTROL =
  "w-full border border-line bg-sunken px-3 py-2 text-label text-ink placeholder:text-ink-faint focus:border-lineHi";

export default function Field({
  label,
  hint,
  error,
  /** Hide the label visually but keep it for screen readers. */
  visuallyHiddenLabel = false,
  className = "",
  children,
}: {
  label: string;
  hint?: ReactNode;
  error?: ReactNode;
  visuallyHiddenLabel?: boolean;
  className?: string;
  children: (props: FieldControlProps) => ReactNode;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;

  // Error wins over hint: pointing at both makes a screen reader read the
  // instruction and the failure as one run-on sentence.
  const describedBy = error ? errorId : hint ? hintId : undefined;

  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      <label
        htmlFor={id}
        className={
          visuallyHiddenLabel
            ? "sr-only"
            : "font-mono text-meta uppercase tracking-[0.1em] text-ink-faint"
        }
      >
        {label}
      </label>

      {children({
        id,
        "aria-describedby": describedBy,
        "aria-invalid": error ? true : undefined,
      })}

      {error ? (
        <p id={errorId} role="alert" className="text-meta text-danger">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="text-meta text-ink-faint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
