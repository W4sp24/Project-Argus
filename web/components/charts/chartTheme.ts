// Recharts is reserved for /insights (§10) — everywhere else uses the single-SVG
// MiniLineChart. Colors are the Phase H terminal-HUD palette tokens (tailwind.config.ts):
// violet = mode.general, cyan = mode.study, rose = the danger token (dataviz six checks pass).
export const SERIES = { violet: "#a78bfa", cyan: "#22d3ee", rose: "#fb7185" };
export const SURFACE = "#0c0916"; // panel

// Recharts renders ticks as SVG <text>, so these sizes sit outside both the
// Tailwind scale and the root rem scaling. They are set here to match `meta`
// (12px) rather than the old 10px, and moved off ink-faint — axis labels are
// the thing you read a chart against, not chrome to be dimmed.
export const AXIS_TICK = { fill: "#9d8fc7", fontSize: 12, fontFamily: "var(--font-mono)" }; // ink-muted

export const TOOLTIP_STYLE = {
  background: "#0c0916", // panel
  border: "1px solid #2c2250", // lineHi
  borderRadius: 0, // square terminal panels — no rounded corners anywhere (§1)
  fontSize: 13,
  color: "#ece7fb", // ink-bright
};
