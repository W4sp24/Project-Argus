"use client";

import { useEffect, useState } from "react";
import Panel from "@/components/Panel";
import Button from "@/components/ui/Button";
import { useUpdater } from "@/lib/updates";

/**
 * UPDATES (§12) — installed version, plus the only way back to an update the
 * user dismissed. The bottom-right prompt still does the announcing; this
 * panel exists because dismissing it would otherwise mean waiting out the 6h
 * background check.
 *
 * Renders nothing in the browser build, where there is no installer to update.
 */
export default function UpdatesPanel() {
  const updater = useUpdater();
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    const argus = (window as unknown as { argus?: { appVersion?: () => Promise<string> } }).argus;
    if (!argus?.appVersion) return;
    let live = true;
    void argus.appVersion().then((value) => {
      if (live) setVersion(value);
    });
    return () => {
      live = false;
    };
  }, []);

  if (!updater.supported) return null;

  const busy = updater.status === "downloading";

  return (
    <Panel
      label="UPDATES"
      headerRight={
        <Button variant="quiet" onClick={updater.check} disabled={busy}>
          {busy ? "DOWNLOADING…" : "CHECK NOW"}
        </Button>
      }
    >
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-label text-ink-faint">INSTALLED</span>
        <span className="font-mono text-label text-ink-bright">{version ?? "…"}</span>
      </div>
      <p className="mt-2 text-meta text-ink-faint">
        {updater.status === "available" && `Version ${updater.version} is available.`}
        {updater.status === "downloading" && `Downloading ${updater.percent}%…`}
        {updater.status === "ready" && `Version ${updater.version} is staged — restart to install.`}
        {updater.status === "error" && `Last check failed: ${updater.message}`}
        {(updater.status === "idle" || updater.status === "none") &&
          "Argus checks for updates on launch and every 6 hours."}
      </p>
    </Panel>
  );
}
