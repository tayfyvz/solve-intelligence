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
              className={`w-full rounded px-3 py-2 text-left text-sm disabled:opacity-50 ${
                selected ? "bg-sky-100 font-semibold text-sky-900" : "hover:bg-slate-100"
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
