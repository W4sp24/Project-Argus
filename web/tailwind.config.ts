import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        void: "#0b0614",
        nebula: "#17092e",
        primary: {
          DEFAULT: "#8b5cf6",
          soft: "#a78bfa",
          deep: "#6d28d9",
        },
        accent: "#d946ef",
        signal: "#22d3ee",
        ink: {
          DEFAULT: "#ede9fe",
          muted: "#9d8fc7",
          faint: "#6b5f94",
        },
      },
      fontFamily: {
        display: ["var(--font-display)", "sans-serif"],
        body: ["var(--font-body)", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      borderRadius: {
        glass: "1.25rem",
      },
      keyframes: {
        drift: {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "33%": { transform: "translate(4%, -3%) scale(1.06)" },
          "66%": { transform: "translate(-3%, 4%) scale(0.97)" },
        },
        breathe: {
          "0%, 100%": { opacity: "0.85", transform: "scale(1)" },
          "50%": { opacity: "1", transform: "scale(1.12)" },
        },
        rise: {
          from: { opacity: "0", transform: "translateY(10px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        drift: "drift 24s ease-in-out infinite",
        "drift-slow": "drift 36s ease-in-out infinite reverse",
        breathe: "breathe 4s ease-in-out infinite",
        rise: "rise 0.5s ease-out both",
      },
    },
  },
  plugins: [],
};
export default config;
