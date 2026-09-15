import type { DragEventHandler, ReactNode } from "react";

interface NotebookPanelProps {
  /**
   * Sentence-case heading. The Notebook redesign drops the `▍MONO.EYEBROW`
   * chrome the rest of Argus uses: mono is reserved here for numbers and keys,
   * and anything you read as a phrase is Inter.
   */
  heading?: string;
  /** One quiet line under the heading — scope, provenance, a caveat. */
  subheading?: string;
  /** 17px for the main column, 15px for the rails. */
  scale?: "lead" | "body";
  /** 24px for the main column, 20px for the rails. */
  pad?: "lg" | "md";
  /** Right-aligned header content: an action link, a count, a filter. */
  headerRight?: ReactNode;
  children: ReactNode;
  className?: string;
  onDragOver?: DragEventHandler<HTMLElement>;
  onDragLeave?: DragEventHandler<HTMLElement>;
  onDrop?: DragEventHandler<HTMLElement>;
}

/**
 * The Notebook's surface — softened corners, slate ground, Inter heading.
 *
 * Deliberately a separate component from `components/Panel.tsx` rather than a
 * variant of it: Panel is shared by all six modes, and giving it a second skin
 * would put every unrelated screen one prop away from changing. The element
 * stays a `<section>` so Playwright can still scope a locator to one panel by
 * its heading.
 */
export default function NotebookPanel({
  heading,
  subheading,
  scale = "lead",
  pad = "lg",
  headerRight,
  children,
  className = "",
  onDragOver,
  onDragLeave,
  onDrop,
}: NotebookPanelProps) {
  const hasHeader = heading || headerRight;
  return (
    <section
      className={`animate-rise rounded-card border border-nb-line bg-nb-panel transition-colors ${
        pad === "lg" ? "p-6" : "p-5"
      } ${className}`}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {hasHeader && (
        <div
          className={`flex flex-wrap items-baseline gap-x-3 gap-y-1 ${
            subheading ? "mb-1" : "mb-4"
          }`}
        >
          {heading && (
            <h2
              className={`min-w-0 font-body font-semibold text-nb-ink ${
                scale === "lead" ? "text-lead" : "text-body"
              }`}
            >
              {heading}
            </h2>
          )}
          {headerRight && (
            <div className="ml-auto flex min-w-0 items-center gap-2">{headerRight}</div>
          )}
        </div>
      )}
      {subheading && <p className="mb-4 text-label text-nb-faint">{subheading}</p>}
      {children}
    </section>
  );
}
