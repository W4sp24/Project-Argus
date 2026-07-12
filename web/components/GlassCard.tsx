import type { DragEventHandler, ReactNode } from "react";

interface GlassCardProps {
  /** Mono eyebrow label rendered as `// LABEL`. */
  label?: string;
  title?: string;
  children: ReactNode;
  className?: string;
  onDragOver?: DragEventHandler<HTMLElement>;
  onDragLeave?: DragEventHandler<HTMLElement>;
  onDrop?: DragEventHandler<HTMLElement>;
}

/** The standard Argus surface: translucent glass over the aurora. */
export default function GlassCard({
  label,
  title,
  children,
  className = "",
  onDragOver,
  onDragLeave,
  onDrop,
}: GlassCardProps) {
  return (
    <section
      className={`glass glass-hover animate-rise p-5 transition-colors ${className}`}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {label && <p className="eyebrow mb-1">{`// ${label}`}</p>}
      {title && <h2 className="mb-3 font-display text-lg font-medium text-ink">{title}</h2>}
      {children}
    </section>
  );
}
