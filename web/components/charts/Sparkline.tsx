/**
 * A trend line small enough to sit inside a dropdown row.
 *
 * Not `MiniLineChart` with a smaller class: that one renders axis labels and a
 * wrapping div sized for a panel, which at this scale is chrome around three
 * pixels of data. This is the line and nothing else.
 *
 * Decorative by intent — `aria-hidden`, because the row it lives in already
 * states the number out loud and a screen reader does not need "trend over
 * time" repeated once per agent.
 */
export default function Sparkline({
  values,
  color,
  className = "h-3.5 w-14",
}: {
  values: number[];
  color?: string;
  className?: string;
}) {
  const stroke = color ?? "var(--ac)";
  if (values.length === 0) {
    return <span aria-hidden className={className} />;
  }

  const width = 56;
  const height = 14;
  // A single reading has no line to draw, so it is mirrored into a flat pair —
  // otherwise the row would show an empty box where a trend should be.
  const points = values.length === 1 ? [values[0], values[0]] : values;
  const max = Math.max(1, ...points);
  const step = width / (points.length - 1);

  const line = points
    .map((value, index) => `${(index * step).toFixed(1)},${(height - (value / max) * height).toFixed(1)}`)
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={`${className} overflow-visible`}
      aria-hidden
      focusable="false"
    >
      <polygon points={`0,${height} ${line} ${width},${height}`} fill={stroke} opacity="0.14" />
      <polyline
        points={line}
        fill="none"
        stroke={stroke}
        strokeWidth="1"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
