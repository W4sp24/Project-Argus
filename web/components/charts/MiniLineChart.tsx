/**
 * Single-SVG line chart for dashboard mini charts (§14, §10): polyline + a
 * 12%-opacity accent area fill underneath, `vector-effect: non-scaling-stroke`
 * so the line stays crisp at any width, `preserveAspectRatio="none"` so it
 * fills the box exactly. One `<svg>` node — no recharts, no extra libraries
 * (§10 perf budget: recharts is reserved for `/insights`).
 */
export default function MiniLineChart({
  values,
  labels,
  height = 64,
  className = "h-16",
}: {
  values: number[];
  labels?: string[];
  height?: number;
  /** Tailwind height class for the rendered SVG (viewBox coordinate space still uses `height`). */
  className?: string;
}) {
  const width = 240;
  const max = Math.max(1, ...values);
  const min = Math.min(0, ...values);
  const range = max - min || 1;
  const stepX = values.length > 1 ? width / (values.length - 1) : 0;

  const points = values.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - min) / range) * height;
    return [x, y] as const;
  });

  const line = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `0,${height} ${line} ${width},${height}`;

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className={`${className} w-full overflow-visible`}
        role="img"
        aria-label="Trend over time"
      >
        <polygon points={area} fill="var(--ac)" opacity="0.12" />
        <polyline
          points={line}
          fill="none"
          stroke="var(--ac)"
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {labels && labels.length > 0 && (
        <div className="mt-1 flex justify-between font-mono text-[9.5px] text-ink-faint">
          {labels.map((label, i) => (
            <span key={i}>{label}</span>
          ))}
        </div>
      )}
    </div>
  );
}
