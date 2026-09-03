import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    // Square terminal panels — no rounded corners anywhere (§1). Overriding the
    // whole scale (not extending) makes every legacy `rounded-*` a silent no-op.
    // `full` stays: circles (logo dot, round task checkboxes, avatar orb) are a
    // deliberate motif in the spec, distinct from rounded panel corners.
    borderRadius: {
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
          // Raised from #5a4f82, which measured 2.79:1 on `void` and 2.70:1 on
          // `panel` — under WCAG AA in both, while carrying ~370 call sites of
          // real information (source metadata, folder counts, empty-state
          // guidance). `panel` is the number that binds: it is lighter than the
          // page, and the densest faint text sits inside it. Verify any change
          // with `node scripts/check-contrast.mjs` before shipping it.
          faint: "#8175AE", // 4.94:1 on void, 4.77:1 on panel
        },
        ok: "#34d399",
        danger: "#fb7185",
        // Warning semantic token. Same hue family as mode.system's amber, but
        // named for what it means (STALE chrome, P2 priority, "needs setup")
        // rather than for the System mode that happens to share the color —
        // three call sites used the literal `amber-400` before this existed.
        warn: "#fbbf24",
        // Muted violet used only by non-mode provenance/status chrome — AUTO,
        // WAITING, AUTH (see web/components/automations/chips.tsx). Deliberately
        // distinct from `mode.general`/--ac: these chips are informational
        // chrome, not the mode's live accent, and must read quieter than it
        // everywhere both appear together.
        auto: {
          DEFAULT: "#8b7bc0",
          line: "#3d2f66",
        },
        // mode accents (CSS var driven at runtime — see --ac in globals.css)
        mode: {
          general: "#a78bfa",
          study: "#22d3ee",
          research: "#e879f9",
          code: "#34d399",
          system: "#fbbf24",
          automations: "#60a5fa",
        },
      },
      fontFamily: {
        body: ["var(--font-body)", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      // Type scale. Under `extend` on purpose: putting `fontSize` directly on
      // `theme` wipes Tailwind's defaults, and ~80 call sites still use
      // text-xs/sm/lg/3xl. Every token is rem so the whole UI tracks the root
      // font-size in globals.css — which is a percentage, so the reader's own
      // browser/OS font setting finally reaches this app.
      //
      // Each token carries a line-height. Before this, 21 elements in the
      // entire app declared one; arbitrary `text-[Npx]` values inherit
      // `normal`, which is why dense panels read as a wall.
      fontSize: {
        micro: ["0.6875rem", { lineHeight: "1.45" }], // 11px — badges, tags
        meta: ["0.75rem", { lineHeight: "1.5" }], // 12px — eyebrows, stat labels
        label: ["0.8125rem", { lineHeight: "1.5" }], // 13px — buttons, chrome
        body: ["0.9375rem", { lineHeight: "1.6" }], // 15px — list rows, prose
        lead: ["1.0625rem", { lineHeight: "1.5" }], // 17px — panel titles
        title: ["1.375rem", { lineHeight: "1.3" }], // 22px — page headings
        display: ["1.75rem", { lineHeight: "1.2" }], // 28px
      },
      spacing: {
        // Right-hand rail on the two-column page grids. Was `340px` copy-pasted
        // into 7 page files; rem so it scales with the root size.
        rail: "21.25rem",
      },
      gridTemplateColumns: {
        // The content + rail split shared by /dashboard, /notebook, /research,
        // /code, /system and the two study sub-pages. Defined once here so the
        // rail width is not seven independent literals that can drift.
        shell: "minmax(0, 1fr) 21.25rem",
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
        // Chat drawer slide-in (§7): transform-only. The drawer unmounts when
        // closed (§10), so entry is an animation rather than a transition.
        drawer: {
          from: { transform: "translateX(105%)" },
          to: { transform: "none" },
        },
      },
      animation: {
        rise: "rise 0.3s ease-out both",
        blink: "blink 1.1s steps(1) infinite",
        // Toast entrance (§5): same rise curve, faster.
        toast: "rise 0.2s ease-out both",
        // Palette / popover entrance (§6): rise at .18s.
        palette: "rise 0.18s ease-out both",
        drawer: "drawer 0.25s ease-out both",
      },
    },
  },
  plugins: [],
};
export default config;
