export interface PagerProps {
  /** Plural noun for the accessible labels: "patents", "versions". */
  noun: string;
  total: number;
  offset: number;
  limit: number;
  /** True while a *list* request is in flight. Paging never touches the editor,
      so this is `listLoading` alone — never the editor's `loading`. */
  busy: boolean;
  onPage(offset: number): void;
}

/** Position first, controls second: "21–40 of 137" answers "where am I?" without making the
 *  user count pages. Page numbers are absent on purpose — they cost a row of buttons. */
export default function Pager({ noun, total, offset, limit, busy, onPage }: PagerProps) {
  // One page of results needs no pager; rendering it disabled is just clutter.
  if (total <= limit) return null;

  // Clamped: `offset` can outrun `total` if rows disappear between requests.
  const first = Math.min(offset, Math.max(total - 1, 0)) + 1;
  const last = Math.min(offset + limit, total);
  const atStart = offset <= 0;
  const atEnd = offset + limit >= total;

  return (
    <div className="flex items-center justify-between gap-2 px-1 pt-2 text-xs text-slate-500">
      <span aria-live="polite">
        {first}–{last} of {total}
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label={`Previous page of ${noun}`}
          disabled={busy || atStart}
          onClick={() => onPage(Math.max(offset - limit, 0))}
          className="focus-ring flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 transition-colors duration-150 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          ‹
        </button>
        <button
          type="button"
          aria-label={`Next page of ${noun}`}
          disabled={busy || atEnd}
          onClick={() => onPage(offset + limit)}
          className="focus-ring flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 transition-colors duration-150 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          ›
        </button>
      </div>
    </div>
  );
}
