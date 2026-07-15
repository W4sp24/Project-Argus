import type { DragEventHandler, ReactNode } from "react";

interface PanelProps {
  /** Mono eyebrow label rendered as `▍LABEL` in the current mode accent. */
  label?: string;
  title?: string;
  children: ReactNode;
  className?: string;
  /** Renders a bordered `PREVIEW` tag next to the label (§8 feature flags). */
  preview?: boolean;
  /** Right-aligned content in the header row (chips, view switchers…). */
  headerRight?: ReactNode;
  onDragOver?: DragEventHandler<HTMLElement>;
  onDragLeave?: DragEventHandler<HTMLElement>;
  onDrop?: DragEventHandler<HTMLElement>;
}

/** The standard Argus surface: square terminal panel — solid, bordered, no blur. */
export default function Panel({
  label,
  title,
  children,
  className = "",
  preview = false,
  headerRight,
  onDragOver,
  onDragLeave,
  onDrop,
}: PanelProps) {
  const hasHeader = label || preview || headerRight;
  return (
    <section
      className={`animate-rise border border-line bg-panel p-5 transition-colors hover:border-lineHi ${className}`}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {hasHeader && (
        <div className="mb-1 flex items-center gap-2">
          {label && <p className="eyebrow">{`▍${label}`}</p>}
          {preview && (
            <span className="border border-[#3d2f66] px-1 py-px font-mono text-[8px] uppercase tracking-[0.16em] text-[#8b7bc0]">
              PREVIEW
            </span>
          )}
          {headerRight && <div className="ml-auto flex items-center">{headerRight}</div>}
        </div>
      )}
      {title && <h2 className="mb-3 font-body text-[15px] font-medium text-ink-bright">{title}</h2>}
      {children}
    </section>
  );
}
