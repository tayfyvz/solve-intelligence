import type { DocumentSummary } from "../types";

export interface DocumentListProps {
  documents: DocumentSummary[];
  selectedId: number | null;
  /** True while a load is in flight: a second selection would only be discarded. */
  disabled: boolean;
  onSelect(id: number): void;
}

/**
 * Purely presentational, props only. The store exists for the *sibling* case
 * (ChatPanel needs the editor Editor owns); passing props one level down is not
 * prop drilling, and reaching into the store from here would make the component
 * untestable without it.
 */
export default function DocumentList({
  documents,
  selectedId,
  disabled,
  onSelect,
}: DocumentListProps) {
  if (!documents.length) {
    return <p className="px-3 py-2 text-sm text-slate-500">No documents.</p>;
  }

  return (
    <ul className="flex flex-col gap-1">
      {documents.map((document) => {
        const selected = document.id === selectedId;
        return (
          <li key={document.id}>
            <button
              type="button"
              disabled={disabled}
              aria-current={selected ? "true" : undefined}
              onClick={() => onSelect(document.id)}
              // Truncated with the full title on hover: a 500-character title
              // would otherwise reflow the 13rem sidebar into a paragraph.
              title={document.title}
              className={`focus-ring w-full truncate rounded-lg px-3 py-2 text-left text-sm transition-colors duration-150 disabled:opacity-50 ${
                selected
                  ? "border border-slate-200 bg-white font-semibold text-slate-900 shadow-sm"
                  : "border border-transparent text-slate-600 hover:bg-slate-200/60 hover:text-slate-900"
              }`}
            >
              {document.title}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
