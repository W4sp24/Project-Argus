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
          background: "#06040c", // void
          color: "#d6cdf0", // ink
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <div
          style={{
            maxWidth: 420,
            padding: 24,
            border: "1px solid #2c2250", // lineHi
            background: "#0c0916", // panel
          }}
        >
          <p style={{ fontSize: 13, letterSpacing: 2, color: "#a78bfa", margin: 0 }}>
            {"// FATAL"}
          </p>
          <h2 style={{ margin: "8px 0 12px", fontSize: 22, fontWeight: 600 }}>
            Argus hit a wall
          </h2>
          <p style={{ fontSize: 15, lineHeight: 1.6, color: "#9d8fc7", margin: 0 }}>
            {error.message || "The app shell itself failed to render."}
          </p>
          <button
            onClick={reset}
            style={{
              marginTop: 16,
              minHeight: 36,
              padding: "8px 16px",
              border: "1px solid #2c2250", // lineHi
              cursor: "pointer",
              color: "#a78bfa",
              background: "#171029", // --ac-bg
              fontSize: 13,
              textTransform: "uppercase",
              letterSpacing: "0.12em",
            }}
          >
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
