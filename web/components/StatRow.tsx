import Link from "next/link";

export interface StatItem {
  href: string;
  label: string;
  value: string | number;
  unit?: string;
}

function Tile({ href, label, value, unit }: StatItem) {
  return (
    <Link
      href={href}
      prefetch={true}
      className="flex min-w-0 flex-col gap-1.5 border border-line bg-panel px-4 py-3 transition-colors hover:border-lineHi"
    >
      <span className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-ink-faint">{label}</span>
      <span className="font-mono text-2xl font-semibold text-ink-bright">
        {value}
        {unit && <span className="ml-1 text-sm font-normal text-ink-muted">{unit}</span>}
      </span>
    </Link>
  );
}

/** 5-tile stat row (§4 General, §9 file plan) — pure presentation, data comes from the caller. */
export default function StatRow({ items }: { items: StatItem[] }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
      {items.map((item) => (
        <Tile key={item.label} {...item} />
      ))}
    </div>
  );
}
