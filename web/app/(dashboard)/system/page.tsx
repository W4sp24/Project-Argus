import CliUsage from "@/components/CliUsage";
import ModeHeader from "@/components/ModeHeader";
import TokenUsage from "@/components/TokenUsage";
import DoctorPanel from "@/components/system/DoctorPanel";
import Integrations from "@/components/system/Integrations";
import McpServers from "@/components/system/McpServers";
import ModelsPanel from "@/components/system/ModelsPanel";
import SetupGuide from "@/components/system/SetupGuide";

/** System mode (§12) — setup guide, health, MCP servers, integrations, models. */
export default function SystemPage() {
  return (
    <>
      <ModeHeader mode="system" greeting="System diagnostics online." />

      <div className="flex flex-col gap-4">
        <SetupGuide />
        <DoctorPanel />
        <McpServers />

        <CliUsage size="large" />

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="flex min-w-0 flex-col gap-4">
            <Integrations />
            <ModelsPanel />
          </div>
          <div className="flex min-w-0 flex-col gap-4">
            <TokenUsage />
          </div>
        </div>
      </div>
    </>
  );
}
