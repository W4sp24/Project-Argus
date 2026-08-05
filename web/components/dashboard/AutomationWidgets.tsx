"use client";

import WidgetShell from "@/components/automations/WidgetShell";
import { useConfirm } from "@/components/ui/useConfirm";
import { useToast } from "@/components/Toast";
import { deleteAutomationWidget, useAutomationWidgets } from "@/lib/api";

/**
 * Automation-fed panels on the dashboard.
 *
 * Auto-place, then take control (Home Assistant's model): a new widget
 * appends automatically, so an automation is useful the moment it first
 * pushes — no configuration step between installing and benefiting. Removing
 * one is what tells the backend the arrangement has become the user's.
 *
 * Renders nothing at all when there are no widgets. An empty panel announcing
 * that you have no automations is worse than the space it occupies, and every
 * existing install starts in exactly that state.
 */
export default function AutomationWidgets() {
  const { data: widgets, mutate } = useAutomationWidgets();
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();

  if (!widgets || widgets.length === 0) return null;

  async function remove(slug: string, title: string) {
    // useConfirm resolves null on cancel and "" on a plain confirm.
    const answer = await confirm({
      label: "Remove widget",
      message: `Remove ${title} from the dashboard?`,
      detail:
        "The automation keeps running in n8n. Its next push will bring the widget back.",
      confirmLabel: "Remove",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await deleteAutomationWidget(slug);
      show(`automations :: removed ${slug}`);
      void mutate();
    } catch (error) {
      show(
        `automations :: could not remove ${slug} — ${
          error instanceof Error ? error.message : "unknown error"
        }`,
        { tone: "error" },
      );
    }
  }

  return (
    <>
      {widgets.map((widget) => (
        <WidgetShell
          key={widget.slug}
          widget={widget}
          actions={
            <button
              type="button"
              onClick={() => void remove(widget.slug, widget.title || widget.slug)}
              className="font-mono text-micro uppercase tracking-[0.14em] text-ink-faint hover:text-danger"
            >
              REMOVE
            </button>
          }
        />
      ))}
      {confirmDialog}
    </>
  );
}
