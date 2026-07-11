import type { ReactNode } from "react";

interface GlassCardProps {
  /** Mono eyebrow label rendered as `// LABEL`. */
  label?: string;
  title?: string;
  children: ReactNode;
  className?: string;
}

/** The standard FRIDAY surface: translucent glass over the aurora. */
export default function GlassCard({ label, title, children, className = "" }: GlassCardProps) {
  return (
    <section className={`glass glass-hover animate-rise p-5 ${className}`}>
      {label && <p className="eyebrow mb-1">{`// ${label}`}</p>}
      {title && <h2 className="mb-3 font-display text-lg font-medium text-ink">{title}</h2>}
      {children}
    </section>
  );
}
