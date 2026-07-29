"use client";

/**
 * Quick Links shared logic — URL sanitization/validation and the "open in
 * the user's real browser" action.
 *
 * This mirrors the BACKEND sanitizer rules (defense in depth: the server is
 * the source of truth, this module exists so the client rejects garbage
 * before it ever reaches `POST/PUT /api/quick-links` and so the panel can
 * give instant feedback). Keep this file free of React imports so it stays
 * trivially unit-testable and importable from both the panel component and
 * an e2e harness.
 *
 * Accept/reject summary for `sanitizeUrl`:
 *  - Accepts: `https://…` (kept), `http://…` (upgraded to `https://`), and
 *    bare hosts with no scheme that contain a dot and no whitespace (e.g.
 *    `example.com/path` → `https://example.com/path`).
 *  - Rejects: empty/whitespace-only input, ASCII control characters anywhere
 *    in the string (0x00–0x1F, 0x7F — stripped first, then the remainder
 *    must still be non-empty), protocol-relative URLs (`//host/...`), and
 *    any explicit non-https scheme — `javascript:`, `data:`, `file:`,
 *    `vbscript:`, `blob:`, `about:`, `mailto:`, `tel:`, etc. The final
 *    candidate must parse via the `URL` constructor and resolve to
 *    `protocol === "https:"` with a non-empty `hostname`.
 *  - On invalid input `sanitizeUrl` THROWS `InvalidUrlError` (does not
 *    return null) so callers can't accidentally treat an unsanitized string
 *    as safe by forgetting a null check.
 */

/** Thrown by {@link sanitizeUrl} when `raw` cannot be turned into a safe,
 * https-only, non-empty-host URL. */
export class InvalidUrlError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidUrlError";
  }
}

/** ASCII control characters (0x00–0x1F, 0x7F), matched anywhere in the string. */
const CONTROL_CHARS_RE = /[\x00-\x1F\x7F]/g;

/** Matches a leading URI scheme, e.g. `javascript:`, `mailto:`, `data:`. */
const HAS_SCHEME_RE = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;

/**
 * Normalize `raw` into an `https://` candidate string, or throw
 * `InvalidUrlError`. Does not itself parse with `URL` — that final
 * validation happens in {@link sanitizeUrl}.
 */
function toHttpsCandidate(raw: string): string {
  const cleaned = raw.replace(CONTROL_CHARS_RE, "").trim();
  if (!cleaned) {
    throw new InvalidUrlError("URL is empty");
  }
  if (cleaned.startsWith("//")) {
    throw new InvalidUrlError(`Protocol-relative URLs are not allowed: ${raw}`);
  }
  if (/^https:\/\//i.test(cleaned)) {
    return cleaned;
  }
  if (/^http:\/\//i.test(cleaned)) {
    return `https://${cleaned.slice("http://".length)}`;
  }
  if (HAS_SCHEME_RE.test(cleaned)) {
    // javascript:, data:, file:, vbscript:, blob:, about:, mailto:, tel:, etc.
    throw new InvalidUrlError(`Unsupported URL scheme: ${raw}`);
  }
  // No scheme at all — only accept if it looks like a bare host: has a dot
  // and contains no whitespace (rules out things like "not a url").
  if (/\s/.test(cleaned) || !cleaned.includes(".")) {
    throw new InvalidUrlError(`Not a valid URL: ${raw}`);
  }
  return `https://${cleaned}`;
}

/**
 * Sanitize and validate a user-supplied Quick Link URL. See the module
 * doc-comment above for the full accept/reject matrix.
 *
 * @throws {InvalidUrlError} if `raw` cannot be resolved to a valid
 *   `https:` URL with a non-empty host.
 */
export function sanitizeUrl(raw: string): string {
  const candidate = toHttpsCandidate(raw);
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new InvalidUrlError(`Not a valid URL: ${raw}`);
  }
  if (parsed.protocol !== "https:" || !parsed.hostname) {
    throw new InvalidUrlError(`Only https URLs with a host are allowed: ${raw}`);
  }
  return parsed.toString();
}

/** True iff {@link sanitizeUrl} would accept `raw` without throwing. */
export function isValidUrl(raw: string): boolean {
  try {
    sanitizeUrl(raw);
    return true;
  } catch {
    return false;
  }
}

// --- custom icons -----------------------------------------------------------

/** Prefix every stored image icon must carry (mirrors the backend). */
const IMAGE_DATA_URI_PREFIX = "data:image/png;base64,";
/** Preset keys accepted by the backend (`^[a-z0-9_-]{1,32}$`). */
const PRESET_KEY_RE = /^[a-z0-9_-]{1,32}$/;
/** ~24 KB cap on a stored image data URI, matching `_MAX_ICON_DATA_LEN`. */
const MAX_ICON_DATA_LEN = 24_000;

/**
 * Client-side mirror of `backend/quick_links.sanitize_icon_value` (defense in
 * depth, same rationale as {@link sanitizeUrl}): the server is the source of
 * truth, but validating here lets the panel reject a too-big image before the
 * request and keeps the picker honest.
 *
 * - `kind === null` → no custom icon; any value is fine (ignored).
 * - `"preset"` → short `[a-z0-9_-]` key.
 * - `"image"`  → a `data:image/png;base64,…` URI within the size cap.
 */
export function isValidIconValue(kind: string | null, value: string | null): boolean {
  if (kind === null) return true;
  if (!value) return false;
  if (kind === "preset") return PRESET_KEY_RE.test(value);
  if (kind === "image") {
    return value.startsWith(IMAGE_DATA_URI_PREFIX) && value.length <= MAX_ICON_DATA_LEN;
  }
  return false;
}

/**
 * Downscale any picked image `File` to a square PNG **data URI** (default
 * 64x64), preserving aspect ratio and centering on a transparent canvas.
 *
 * This is the single normalization point for *both* icon-upload paths — the
 * web `<input type="file">` and the Electron `argus.pickIcon()` dialog — so the
 * value stored in SQLite is always a small `data:image/png` regardless of the
 * source format (png/jpg/webp/svg) or original dimensions. The result renders
 * under the existing `img-src 'self' data: blob:` CSP with no policy change.
 *
 * Rejects (rejects the promise) if the file isn't a decodable image or the
 * downscaled result would still exceed the stored-size cap.
 */
export function fileToPngDataUrl(file: File | Blob, size = 64): Promise<string> {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(objectUrl);
      try {
        const canvas = document.createElement("canvas");
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          reject(new Error("Canvas is not available"));
          return;
        }
        // "contain" fit: scale the longest side to `size`, center the rest.
        const scale = Math.min(size / img.width, size / img.height) || 1;
        const w = Math.max(1, Math.round(img.width * scale));
        const h = Math.max(1, Math.round(img.height * scale));
        ctx.drawImage(img, (size - w) / 2, (size - h) / 2, w, h);
        const dataUrl = canvas.toDataURL("image/png");
        if (!dataUrl.startsWith(IMAGE_DATA_URI_PREFIX) || dataUrl.length > MAX_ICON_DATA_LEN) {
          reject(new Error("Image is too large after downscaling"));
          return;
        }
        resolve(dataUrl);
      } catch (error) {
        reject(error instanceof Error ? error : new Error("Could not process image"));
      }
    };
    img.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("Could not load image"));
    };
    img.src = objectUrl;
  });
}

/**
 * Decode a `data:<mime>;base64,<body>` URI to a `Blob` without any network
 * request. Used for the Electron `argus.pickIcon()` result — `fetch()` on a
 * `data:` URL can be blocked by the loopback `connect-src` CSP, so we parse it
 * by hand instead. Throws on a non-base64 `data:` URI.
 */
function dataUrlToBlob(dataUrl: string): Blob {
  const match = /^data:([^;,]*)?(;base64)?,(.*)$/s.exec(dataUrl);
  if (!match) throw new Error("Not a data URI");
  const mime = match[1] || "application/octet-stream";
  const isBase64 = Boolean(match[2]);
  const body = match[3];
  const binary = isBase64 ? atob(body) : decodeURIComponent(body);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

/**
 * Turn a raw `data:` URI (e.g. the value the Electron main process returns from
 * `argus.pickIcon()`) into a downscaled PNG data URI via {@link fileToPngDataUrl}.
 */
export async function dataUrlToPngDataUrl(dataUrl: string, size = 64): Promise<string> {
  return fileToPngDataUrl(dataUrlToBlob(dataUrl), size);
}

/**
 * Open a Quick Link in the user's real browser. Under the Electron desktop
 * shell this goes through the `window.argus.openExternal` preload bridge
 * (see `desktop/preload.js` / `desktop/main.js`'s `shell:open` handler,
 * which itself only allows `https:`/`obsidian:`); in the plain web app it
 * falls back to `window.open` in a new tab.
 *
 * The URL is always sanitized first via {@link sanitizeUrl} so an invalid
 * or unsafe string can never reach `openExternal`/`window.open`. If
 * sanitization fails this function silently does nothing (it does not
 * throw) — callers that want to surface an error to the user should call
 * {@link isValidUrl} (or `sanitizeUrl` directly) beforehand.
 */
export function openExternalUrl(url: string): void {
  let safe: string;
  try {
    safe = sanitizeUrl(url);
  } catch {
    return;
  }
  const w = window as unknown as { argus?: { openExternal?: (u: string) => Promise<boolean> | boolean } };
  if (w.argus?.openExternal) {
    void w.argus.openExternal(safe);
  } else {
    window.open(safe, "_blank", "noopener,noreferrer");
  }
}
