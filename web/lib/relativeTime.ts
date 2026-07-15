/**
 * Shared "n ago" formatter for mono metadata lines (§4 Code: `✎ 2h ago`,
 * §12 System: last Claude Code session stamp). Accepts any ISO-ish
 * timestamp `Date` can parse; returns a stable placeholder for bad input
 * instead of throwing (vault timestamps are user data, not guaranteed clean).
 */
export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso.includes("T") || iso.includes(" ") ? iso.replace(" ", "T") : `${iso}T00:00:00`);
  const ms = Date.now() - then.getTime();
  if (Number.isNaN(ms)) return "—";
  const minutes = Math.round(ms / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.round(days / 30);
  return `${months}mo ago`;
}
