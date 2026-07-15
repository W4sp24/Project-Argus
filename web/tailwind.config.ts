import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    // Square terminal panels — no rounded corners anywhere (§1). Overriding the
    // whole scale (not extending) makes every legacy `rounded-*` a silent no-op.
    // `full` stays: circles (logo dot, round task checkboxes, avatar orb) are a
    // deliberate motif in the spec, distinct from rounded panel corners.
    borderRadius: {
      glass: "0px", // LEGACY — remove in Phase H
      full: "9999px",
    },
    extend: {
      colors: {
        void: "#06040c", // page background
        panel: "#0c0916", // card surface
        sunken: "#06040c", // inputs / nested surfaces (same as void)
        line: "#1e1733", // all borders
        lineHi: "#2c2250", // hovered/active borders
        ink: {
          DEFAULT: "#d6cdf0",
          bright: "#ece7fb",
          muted: "#9d8fc7",
          faint: "#5a4f82",
        },
        ok: "#34d399",
        danger: "#fb7185",
        // mode accents (CSS var driven at runtime — see --ac in globals.css)
        mode: {
          general: "#a78bfa",
          study: "#22d3ee",
          research: "#e879f9",
          code: "#34d399",
          system: "#fbbf24",
        },
        // LEGACY — remove in Phase H. Existing components still reference these
        // names; Tailwind silently drops unknown classes, so removing the aliases
        // before the full re-skin would break every page with zero build errors.
        primary: {
          DEFAULT: "#a78bfa",
          soft: "#a78bfa",
          deep: "#7c5ce0",
        },
        accent: "#e879f9",
        signal: "#22d3ee",
        nebula: "#0c0916",
      },
      fontFamily: {
        display: ["var(--font-body)", "sans-serif"], // LEGACY — points at Inter; remove in Phase H
        body: ["var(--font-body)", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      keyframes: {
        rise: {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "none" },
        },
        blink: {
          "0%, 55%": { opacity: "1" },
          "56%, 100%": { opacity: "0" },
        },
      },
      animation: {
        rise: "rise 0.3s ease-out both",
        blink: "blink 1.1s steps(1) infinite",
        // Toast entrance (§5): same rise curve, faster.
        toast: "rise 0.2s ease-out both",
      },
    },
  },
  plugins: [],
};
export default config;
