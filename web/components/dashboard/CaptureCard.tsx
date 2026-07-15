"use client";

import { useState } from "react";
import Panel from "@/components/Panel";

export default function CaptureCard({ onCaptured }: { onCaptured?: () => void }) {
  const [capture, setCapture] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  async function submitCapture(event: React.FormEvent) {
    event.preventDefault();
    const text = capture.trim();
    if (!text) return;
    setCapture("");
    const response = await fetch("/api/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const payload = await response.json();
    setStatus(response.ok ? `Captured → ${payload.path}` : `Capture failed: ${payload.detail}`);
    onCaptured?.();
    setTimeout(() => setStatus(null), 5000);
  }

  return (
    <Panel label="CAPTURE" title="Quick capture">
      <form onSubmit={submitCapture} className="flex gap-2">
        <input
          value={capture}
          onChange={(event) => setCapture(event.target.value)}
          placeholder="e.g. email prof about thesis"
          className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-sm placeholder:text-ink-faint focus:border-primary-soft/50 focus:outline-none"
        />
        <button
          type="submit"
          disabled={!capture.trim()}
          className="shrink-0 rounded-xl bg-gradient-to-r from-primary to-accent px-4 py-2.5 font-display text-sm text-white disabled:opacity-40"
        >
          Save
        </button>
      </form>
      {status && <p className="mt-3 font-mono text-[11px] text-primary-soft">{status}</p>}
    </Panel>
  );
}
