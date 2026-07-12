"use client";

/**
 * Last-resort boundary: replaces the root layout when even it crashes,
 * so it must render its own <html>/<body> and carry its own styling.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#0b0614",
          color: "#ede9fe",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <div
          style={{
            maxWidth: 420,
            padding: 24,
            borderRadius: 20,
            border: "1px solid rgba(255,255,255,0.1)",
            background: "rgba(23,9,46,0.7)",
          }}
        >
          <p style={{ fontSize: 12, letterSpacing: 2, color: "#6b5f94", margin: 0 }}>
            {"// FATAL"}
          </p>
          <h2 style={{ margin: "8px 0 12px", fontWeight: 600 }}>Argus hit a wall</h2>
          <p style={{ fontSize: 14, color: "#9d8fc7", margin: 0 }}>
            {error.message || "The app shell itself failed to render."}
          </p>
          <button
            onClick={reset}
            style={{
              marginTop: 16,
              padding: "8px 16px",
              borderRadius: 12,
              border: "none",
              cursor: "pointer",
              color: "white",
              background: "linear-gradient(90deg, #8b5cf6, #d946ef)",
              fontSize: 14,
            }}
          >
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
