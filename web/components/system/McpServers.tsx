"use client";

import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";

interface McpCard {
  name: string;
  port: number;
  status: "WIRED" | "NOT CONNECTED";
  tools: string[];
  detail: string;
}

const SERVERS: McpCard[] = [
  {
    name: "mcp-obsidian",
    port: 27124,
    status: "WIRED",
    tools: ["read_note", "search_vault", "append_review_queue"],
    detail: "writes still go via the review queue",
  },
  {
    name: "mcp-gmail",
    port: 27125,
    status: "NOT CONNECTED",
    tools: ["search_mail", "read_thread", "extract_tasks"],
    detail: "read-only scopes by design, extractions land in the review queue",
  },
];

/** MCP.SERVERS (§12) — static status cards for the two v1 MCP servers. */
export default function McpServers() {
  const { show } = useToast();

  return (
    <Panel label="MCP.SERVERS">
      <div className="grid gap-3 sm:grid-cols-2">
        {SERVERS.map((server) => {
          const wired = server.status === "WIRED";
          return (
            <div key={server.name} className="border border-line p-3">
              <div className="flex items-center gap-2">
                <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${wired ? "bg-ok" : "bg-ink-faint"}`} />
                <p className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink">
                  {server.name} <span className="text-ink-faint">:{server.port}</span>
                </p>
                {wired ? (
                  <span className="shrink-0 border border-ok px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-ok">
                    WIRED
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => show("connect :: run `argus mcp add gmail`")}
                    className="shrink-0 border border-line px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-lineHi hover:text-ink"
                  >
                    CONNECT
                  </button>
                )}
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {server.tools.map((tool) => (
                  <span key={tool} className="border border-line px-1.5 py-0.5 font-mono text-[10px] text-ink-faint">
                    {tool}
                  </span>
                ))}
              </div>
              <p className="mt-2 text-[11.5px] leading-relaxed text-ink-muted">{server.detail}</p>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
