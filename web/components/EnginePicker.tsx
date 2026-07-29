"use client";

import { useRef, useState } from "react";
import AddModelDialog from "@/components/system/AddModelDialog";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import { useModels, type KeyState, type ModelInfo } from "@/lib/api";
import { BUILTIN_MODELS, setModel, useModelSync, useSelectedModel } from "@/lib/models";
import { useUi } from "@/lib/ui";

/**
 * Engine picker (§7) — replaces the old ModelSelect dropdown.
 *
 * Models are grouped by **where they run**, because that is the fact a person
 * actually decides on: whether their notes leave the machine, and whether the
 * answer costs money. The previous UI carried that in an 8px `LOCAL`/`HOSTED`
 * chip whose explanation lived in a `title` tooltip — invisible to touch, to
 * keyboards, and to anyone not hovering. Here the group heading says it, so
 * the chip is gone rather than merely bigger.
 */

interface Engine {
  name: string;
  local: boolean;
  /** Where the answer comes from and what it costs. */
  source: string;
  /** What leaves this computer. */
  privacy: string;
  isDefault: boolean;
}

function hostOf(endpoint?: string | null): string | null {
  if (!endpoint) return null;
  try {
    return new URL(endpoint).hostname;
  } catch {
    return null;
  }
}

function toEngine(model: ModelInfo): Engine {
  const base = { name: model.name, local: model.local, isDefault: model.default };
  if (model.provider === "anthropic") {
    return {
      ...base,
      source: "claude code · your subscription",
      privacy: "note excerpts are sent to Anthropic",
    };
  }
  if (model.provider === "anthropic-api") {
    return {
      ...base,
      source: "anthropic · your api key",
      privacy: "note excerpts are sent to Anthropic",
    };
  }
  if (model.local) {
    return {
      ...base,
      source: "free · runs offline",
      privacy: "your notes never leave this computer",
    };
  }
  const host = hostOf(model.endpoint);
  return {
    ...base,
    source: `${host ?? "hosted"} · ${describeKey(model.key_state)}`,
    privacy: "note excerpts are sent to this provider",
  };
}

/** How a stored key reads in the picker's one-line summary. */
function describeKey(state: KeyState | undefined): string {
  // "unknown" is not "absent": telling someone no key is stored when the
  // keyring merely failed to open sends them to re-enter one they already have.
  if (state === "unknown") return "key status unknown";
  return state === "present" ? "your api key" : "no key stored";
}

/** Offline shape — the backend is unreachable, so show what config.py ships. */
const FALLBACK: Engine[] = BUILTIN_MODELS.map((entry, index) => ({
  name: entry.name,
  local: false,
  source: "claude code · your subscription",
  privacy: "note excerpts are sent to Anthropic",
  isDefault: index === 0,
}));

export default function EnginePicker() {
  const { enginePickerOpen } = useUi();
  // Mounted once in the dashboard layout and always rendered (the body is what
  // unmounts), so this is the one place guaranteed to be watching the registry
  // — hence the stale-selection reconciliation rides here rather than needing
  // its own component in the tree.
  useModelSync();
  if (!enginePickerOpen) return null;
  return <EnginePickerBody />;
}

function EnginePickerBody() {
  const { setEnginePickerOpen } = useUi();
  const selected = useSelectedModel();
  const { data, mutate } = useModels();
  const [adding, setAdding] = useState(false);

  const engines = data && data.length > 0 ? data.map(toEngine) : FALLBACK;
  const local = engines.filter((engine) => engine.local);
  const remote = engines.filter((engine) => !engine.local);

  // Arrow keys walk both groups as one sequence — the split is a reading aid,
  // not a navigation boundary.
  const ordered = [...local, ...remote];
  const cardRefs = useRef<(HTMLButtonElement | null)[]>([]);
  // Open focused on the engine currently in use, not on the close button that
  // happens to come first in the DOM — otherwise the arrow keys the radiogroup
  // exists for do nothing until you have tabbed past the header.
  const activeCardRef = useRef<HTMLButtonElement | null>(null);

  function choose(name: string) {
    setModel(name);
    setEnginePickerOpen(false);
  }

  function move(from: number, delta: number) {
    const next = (from + delta + ordered.length) % ordered.length;
    // Radio-group convention: arrowing moves selection, not just focus.
    setModel(ordered[next].name);
    cardRefs.current[next]?.focus();
  }

  function onCardKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        event.preventDefault();
        move(index, 1);
        break;
      case "ArrowLeft":
      case "ArrowUp":
        event.preventDefault();
        move(index, -1);
        break;
      case "Home":
        event.preventDefault();
        move(0, 0);
        break;
      case "End":
        event.preventDefault();
        move(ordered.length - 1, 0);
        break;
      default:
        break;
    }
  }

  function renderGroup(heading: string, note: string, group: Engine[], offset: number) {
    if (group.length === 0) return null;
    return (
      <div className="mb-4 last:mb-0">
        <p className="mb-2 font-mono text-meta uppercase tracking-[0.14em] text-ink-faint">
          {heading}
          <span className="ml-2 normal-case tracking-normal text-ink-faint/70">{note}</span>
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          {group.map((engine, indexInGroup) => {
            const index = offset + indexInGroup;
            const active = engine.name === selected;
            return (
              <button
                key={engine.name}
                ref={(el) => {
                  cardRefs.current[index] = el;
                  if (active) activeCardRef.current = el;
                }}
                type="button"
                role="radio"
                aria-checked={active}
                // Roving tabindex: one stop for the whole group, arrows inside.
                tabIndex={active || (!ordered.some((e) => e.name === selected) && index === 0) ? 0 : -1}
                onClick={() => choose(engine.name)}
                onKeyDown={(event) => onCardKeyDown(event, index)}
                className={`flex flex-col border p-3 text-left transition-colors ${
                  active
                    ? "border-[var(--ac)] bg-[var(--ac-bg)]"
                    : "border-line hover:border-lineHi"
                }`}
              >
                <span className="flex items-baseline gap-2">
                  <span className="min-w-0 flex-1 truncate text-body text-ink-bright">
                    {engine.name}
                  </span>
                  {engine.isDefault && (
                    <span className="shrink-0 font-mono text-micro uppercase tracking-[0.14em] text-ink-faint">
                      default
                    </span>
                  )}
                </span>
                <span className="mt-1 text-meta text-ink-muted">{engine.source}</span>
                <span className="mt-0.5 text-meta text-ink-faint">{engine.privacy}</span>
                <span
                  className={`mt-3 border px-2 py-1 text-center font-mono text-micro uppercase tracking-[0.14em] ${
                    active
                      ? "border-[var(--ac)] text-[var(--ac)]"
                      : "border-line text-ink-faint"
                  }`}
                >
                  {active ? "✓ in use" : "use"}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <>
      <Dialog
        label="Select engine"
        onClose={() => setEnginePickerOpen(false)}
        align="center"
        className="w-[42.5rem] max-w-[calc(100vw-2rem)] p-5"
        initialFocusRef={activeCardRef}
      >
        <div className="mb-4 flex items-baseline gap-3">
          <p className="eyebrow">▍SELECT.ENGINE</p>
          <p className="min-w-0 flex-1 truncate text-meta text-ink-faint">
            now using <span className="text-ink-muted">{selected}</span>
          </p>
          <Button
            variant="ghost"
            aria-label="Close"
            onClick={() => setEnginePickerOpen(false)}
            className="-mr-1"
          >
            ✕
          </Button>
        </div>

        <div role="radiogroup" aria-label="Engine">
          {renderGroup(
            "On this computer",
            "nothing leaves the machine",
            local,
            0,
          )}
          {renderGroup(
            "Sent to a provider",
            "note excerpts travel over the network",
            remote,
            local.length,
          )}
        </div>

        <div className="mt-2 border-t border-line pt-3">
          <Button variant="ghost" onClick={() => setAdding(true)} className="normal-case">
            + Add a model…
          </Button>
        </div>
      </Dialog>

      {/* Rendered here rather than routed to /system: adding a model used to
          mean abandoning whatever conversation prompted the need for one. */}
      {adding && (
        <AddModelDialog onClose={() => setAdding(false)} onAdded={() => mutate()} />
      )}
    </>
  );
}

/**
 * The control that opens the picker. Shared by the top bar, the chat drawer,
 * `/chat`, the study header, and the course hub, so the accessible name stays
 * `Model: <name>` everywhere — the e2e suite addresses it that way.
 */
export function EngineTrigger({
  size = "sm",
  className = "",
}: {
  size?: "sm" | "md";
  className?: string;
}) {
  const selected = useSelectedModel();
  const { data } = useModels();
  const { setEnginePickerOpen } = useUi();
  const local = data?.find((model) => model.name === selected)?.local ?? false;

  return (
    <Button
      size={size}
      aria-haspopup="dialog"
      aria-label={`Model: ${selected}`}
      onClick={() => setEnginePickerOpen(true)}
      className={`normal-case tracking-normal ${className}`}
    >
      <span
        aria-hidden
        title={local ? "Runs on this computer" : "Sent to a provider"}
        className={`h-2 w-2 shrink-0 rounded-full ${
          local ? "bg-ok" : "border border-ink-faint"
        }`}
      />
      <span className="max-w-[11rem] truncate">{selected}</span>
      <span aria-hidden className="text-ink-faint">
        ▾
      </span>
    </Button>
  );
}
