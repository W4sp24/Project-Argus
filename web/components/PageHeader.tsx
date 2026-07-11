interface PageHeaderProps {
  label: string;
  title: string;
  subtitle?: string;
}

/** Consistent page opener: mono eyebrow, display title, quiet subtitle. */
export default function PageHeader({ label, title, subtitle }: PageHeaderProps) {
  return (
    <header className="mb-8 animate-rise">
      <p className="eyebrow mb-2">{`// ${label}`}</p>
      <h1 className="font-display text-3xl font-semibold tracking-tight text-ink md:text-4xl">
        {title}
      </h1>
      {subtitle && <p className="mt-2 max-w-xl text-sm text-ink-muted">{subtitle}</p>}
    </header>
  );
}
