import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Set by the desktop build (see desktop/scripts/stage.mjs). Packaged mode runs
// the standalone server inside Electron and talks to a backend on a port that
// isn't known until launch, so it resolves API URLs at runtime via the preload
// bridge (web/lib/api.ts) instead of through a rewrite.
const packaged = process.env.ARGUS_PACKAGED === "1";

/**
 * Loopback-only CSP. `connect-src` is the load-bearing rule: the chat surface
 * renders agent output through react-markdown, so a prompt-injected response
 * must not be able to reach a remote origin with vault content. Scripts need
 * 'unsafe-inline' for Next's hydration payload (nonces would require a
 * middleware, which isn't worth it for a local-only app).
 */
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self' http://127.0.0.1:* http://localhost:* ws://127.0.0.1:* ws://localhost:*",
  "frame-ancestors 'none'",
  "object-src 'none'",
  "base-uri 'self'",
].join("; ");

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Traced server bundle (~60MB) that Electron runs with its own embedded
  // Node via ELECTRON_RUN_AS_NODE, so end users need no Node install.
  output: packaged ? "standalone" : undefined,
  // Keep file tracing inside web/ — otherwise it walks up into .venv and
  // traces the whole Python tree. Top-level in Next 15; still experimental in
  // 14.2, where an unrecognized top-level key is warned about and ignored.
  experimental: { outputFileTracingRoot: __dirname },

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },

  async rewrites() {
    // Next bakes rewrites into .next/routes-manifest.json at build time, so
    // they can't carry a runtime port. Dev and `argus web` use the fixed 8000
    // backend; packaged mode omits them entirely.
    if (packaged) return [];
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
      {
        source: "/health",
        destination: "http://127.0.0.1:8000/health",
      },
    ];
  },
};

export default nextConfig;
