const RADIUS = 40;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

interface ProgressRingProps {
  value: number;
  total: number;
  /** What the two numbers mean, for a screen reader. */
  label?: string;
  size?: number;
}

/**
 * The mastered-vs-total ring (Notebook Redesign v2).
 *
 * Inline SVG rather than a charting library on purpose: `/notebook` and
 * `/notebook/course/[code]` are already at or over the bundle budget, and this
 * is two circles and two numbers. `stroke-dashoffset` does the whole job.
 *
 * The `<title>` is what a screen reader reads — the numbers inside the ring are
 * `aria-hidden` because on their own ("15", "of 24") they say nothing about
 * what was counted.
 */
export default function ProgressRing({
  value,
  total,
  label = "mastered",
  size = 96,
}: ProgressRingProps) {
  const share = total > 0 ? Math.min(1, value / total) : 0;
  return (
    <svg
      viewBox="0 0 96 96"
      width={size}
      height={size}
      role="img"
      className="shrink-0"
      style={{ width: size, height: size }}
    >
      <title>{`${value} of ${total} ${label}`}</title>
      <circle
        cx="48"
        cy="48"
        r={RADIUS}
        fill="none"
        strokeWidth="9"
        className="stroke-nb-track"
      />
      <circle
        cx="48"
        cy="48"
        r={RADIUS}
        fill="none"
        strokeWidth="9"
        strokeLinecap="round"
        stroke="var(--ac)"
        strokeDasharray={CIRCUMFERENCE}
        strokeDashoffset={CIRCUMFERENCE * (1 - share)}
        transform="rotate(-90 48 48)"
      />
      {/* `fontSize` as an SVG attribute, not a `text-*` class: these are user
          units in the 96-unit viewBox, so they scale with the ring. A rem token
          would resolve against the root font size and break the layout. */}
      <text
        x="48"
        y="45"
        textAnchor="middle"
        fontSize="21"
        fontWeight="600"
        aria-hidden="true"
        className="fill-nb-ink font-body"
      >
        {value}
      </text>
      <text
        x="48"
        y="61"
        textAnchor="middle"
        fontSize="11"
        aria-hidden="true"
        className="fill-nb-body font-body"
      >
        {`of ${total}`}
      </text>
    </svg>
  );
}
