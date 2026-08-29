"use client";

import { useRef, useState } from "react";
import { PresetIcon, PRESET_ICON_KEYS } from "@/lib/icons";
import { dataUrlToPngDataUrl, fileToPngDataUrl } from "@/lib/quickLinks";

/** The three icon fields a Quick Link add/edit form carries as one unit —
 * mirrors `QuickLinkIconFields` in `web/lib/api.ts` but always fully
 * populated (never partial) so the picker can be a simple controlled
 * component. */
export interface IconSelection {
  icon: string | null;
  icon_kind: "preset" | "image" | null;
  icon_value: string | null;
}

interface IconPickerProps {
  value: IconSelection;
  onChange: (next: IconSelection) => void;
  /** Surfaces upload/decoding failures — the panel passes `useToast().show`. */
  onError?: (message: string) => void;
}

/** Shape of the Electron `argus.pickIcon()` bridge (see `desktop/preload.js`).
 * Detected the same way `openExternalUrl` detects `argus.openExternal`. */
interface PickIconBridge {
  pickIcon?: () => Promise<null | { ok: false; error: string } | { ok: true; name: string; dataUrl: string }>;
}

/**
 * Controlled icon selector shared by the QUICK.LINKS add form and inline edit
 * form. Three ways to pick an icon, all collapsing to the same
 * `IconSelection` shape:
 *  - a bundled preset SVG (grid of `PRESET_ICON_KEYS`),
 *  - a terminal glyph (free-text, single/few chars),
 *  - an uploaded image (Electron native dialog when available, else a hidden
 *    `<input type="file">`), downscaled client-side to a small PNG data URI.
 * "Clear" resets to no custom icon (falls back to the default `↗` glyph via
 * `QuickLinkIcon`).
 */
export default function IconPicker({ value, onChange, onError }: IconPickerProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  function reportError(message: string) {
    onError?.(message);
  }

  async function applyImageFile(file: File | Blob) {
    setUploading(true);
    try {
      const pngDataUrl = await fileToPngDataUrl(file);
      onChange({ icon: null, icon_kind: "image", icon_value: pngDataUrl });
    } catch (error) {
      reportError(error instanceof Error ? error.message : "Could not process image");
    } finally {
      setUploading(false);
    }
  }

  async function handleUploadClick() {
    const bridge = (window as unknown as { argus?: PickIconBridge }).argus;
    if (bridge?.pickIcon) {
      setUploading(true);
      try {
        const result = await bridge.pickIcon();
        if (result === null) return; // cancelled
        if (!result.ok) {
          reportError(result.error);
          return;
        }
        const pngDataUrl = await dataUrlToPngDataUrl(result.dataUrl);
        onChange({ icon: null, icon_kind: "image", icon_value: pngDataUrl });
      } catch (error) {
        reportError(error instanceof Error ? error.message : "Could not process image");
      } finally {
        setUploading(false);
      }
      return;
    }
    fileInputRef.current?.click();
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-1">
        {PRESET_ICON_KEYS.map((key) => {
          const selected = value.icon_kind === "preset" && value.icon_value === key;
          return (
            <button
              key={key}
              type="button"
              aria-label={`Use ${key} icon`}
              onClick={() => onChange({ icon: null, icon_kind: "preset", icon_value: key })}
              className={`flex h-6 w-6 shrink-0 items-center justify-center border transition-colors ${
                selected
                  ? "border-lineHi text-[var(--ac)]"
                  : "border-line text-ink-faint hover:border-lineHi hover:text-[var(--ac)]"
              }`}
            >
              <PresetIcon name={key} size={14} />
            </button>
          );
        })}
      </div>
      <div className="flex items-center gap-2">
        <input
          value={value.icon ?? ""}
          onChange={(event) => {
            const next = event.target.value;
            onChange({ icon: next || null, icon_kind: null, icon_value: null });
          }}
          placeholder="↗"
          aria-label="Icon glyph"
          className="w-10 shrink-0 border border-line bg-transparent px-1 py-1 text-center text-body placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
        />
        <button
          type="button"
          onClick={handleUploadClick}
          disabled={uploading}
          aria-label="Upload icon image"
          className="shrink-0 border border-line px-2 py-1 font-mono text-meta uppercase tracking-[0.12em] text-ink-faint transition-colors hover:border-lineHi hover:text-[var(--ac)] disabled:opacity-70"
        >
          {uploading ? "…" : "upload"}
        </button>
        <button
          type="button"
          onClick={() => onChange({ icon: null, icon_kind: null, icon_value: null })}
          aria-label="Clear icon"
          className="shrink-0 font-mono text-meta uppercase tracking-[0.12em] text-ink-faint hover:text-danger"
        >
          clear
        </button>
        {value.icon_kind === "image" && value.icon_value ? (
          // eslint-disable-next-line @next/next/no-img-element -- data: URI preview, not a remote asset
          <img src={value.icon_value} alt="" className="h-4 w-4 shrink-0 object-contain" />
        ) : null}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/svg+xml"
          data-testid="icon-file-input"
          className="hidden"
          onChange={async (event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (!file) return;
            await applyImageFile(file);
          }}
        />
      </div>
    </div>
  );
}
