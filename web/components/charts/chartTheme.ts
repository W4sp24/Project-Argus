// Recharts is reserved for /insights (§10) — everywhere else uses the single-SVG
// MiniLineChart. Colors are the Phase H terminal-HUD palette tokens (tailwind.config.ts):
// violet = mode.general, cyan = mode.study, rose = the danger token (dataviz six checks pass).
export const SERIES = { violet: "#a78bfa", cyan: "#22d3ee", rose: "#fb7185" };
export const SURFACE = "#0c0916"; // panel

export const AXIS_TICK = { fill: "#5a4f82", fontSize: 10, fontFamily: "var(--font-mono)" }; // ink-faint

export const TOOLTIP_STYLE = {
  background: "#0c0916", // panel
  border: "1px solid #2c2250", // lineHi
  borderRadius: 0, // square terminal panels — no rounded corners anywhere (§1)
  fontSize: 12,
  color: "#ece7fb", // ink-bright
};
