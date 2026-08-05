"use client";

// A 500-row push (a workflow that forgot to paginate) must degrade
// gracefully, not hang the tab — this is a dashboard tile, not a data grid.
const MAX_ROWS = 200;

interface TableData {
  columns: string[];
  rows: unknown[][];
  /** True rows length before the cap, for the "+N more" footnote. */
  totalRows: number;
}

function parseTable(payload: unknown): TableData {
  if (typeof payload !== "object" || payload === null) return { columns: [], rows: [], totalRows: 0 };
  const raw = payload as Record<string, unknown>;
  const columns = Array.isArray(raw.columns)
    ? raw.columns.filter((c): c is string => typeof c === "string")
    : [];
  const rawRows = Array.isArray(raw.rows) ? raw.rows : [];
  const rows: unknown[][] = [];
  for (const row of rawRows) {
    if (!Array.isArray(row)) continue;
    if (rows.length < MAX_ROWS) rows.push(row);
  }
  return { columns, rows, totalRows: rawRows.length };
}

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return "—";
  }
}

export default function Table({ payload }: { payload: unknown }) {
  const { columns, rows, totalRows } = parseTable(payload);
  if (columns.length === 0) {
    return <p className="text-label text-ink-faint">This table has no columns.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-max border-collapse text-label">
        <thead>
          <tr className="border-b border-line">
            {columns.map((col, i) => (
              <th
                key={i}
                className="whitespace-nowrap px-2 py-1 text-left font-mono text-micro uppercase tracking-[0.1em] text-ink-faint"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rIdx) => (
            <tr key={rIdx} className="border-b border-line last:border-b-0">
              {columns.map((_, cIdx) => (
                <td key={cIdx} className="max-w-[16rem] truncate px-2 py-1 text-ink">
                  {cellText(row[cIdx])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {totalRows > rows.length && (
        <p className="mt-2 font-mono text-meta text-ink-faint">
          +{totalRows - rows.length} more row{totalRows - rows.length === 1 ? "" : "s"} not shown
        </p>
      )}
    </div>
  );
}
