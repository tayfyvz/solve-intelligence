import type { ReactNode } from "react";

import InlineRename from "../../components/ui/InlineRename";

export interface TreeRowProps {
  /** The row's primary text. Truncated — long titles are readable in the banner. */
  label: string;
  /** The second line: version count, saved-at. */
  meta: ReactNode;
  /** Small pill after the label, e.g. "Open". */
  badge?: ReactNode;
  selected: boolean;
  /** True while the editor pane is busy: a second selection would be discarded. */
  disabled: boolean;
  /** Names both the pencil button and the rename field, e.g. `Rename version 3`. */
  renameLabel: string;
  editing: boolean;
  /** Set on the patent rows only, which expand to show their versions. */
  expanded?: boolean;
  /** Names the delete button, e.g. `Delete version 3`. Omitted entirely on patent
   *  rows — deleting a whole patent is a different, larger operation. */
  deleteLabel?: string;
  onSelect(): void;
  onEdit(): void;
  onCancelEdit(): void;
  /** Resolves to an error sentence to show under the field, or null on success. */
  onRename(value: string): Promise<string | null>;
  /** Present only where delete is offered at all — version rows with more than
   *  one version. Its absence is what hides the button, not a boolean flag. */
  onDelete?(): void;
}

/** One row shape for both levels of the tree: patents and versions differ only in their text
 *  and indent. Props only, so a row can be rendered in a test without the store. */
export default function TreeRow({
  label,
  meta,
  badge,
  selected,
  disabled,
  renameLabel,
  editing,
  expanded,
  deleteLabel,
  onSelect,
  onEdit,
  onCancelEdit,
  onRename,
  onDelete,
}: TreeRowProps) {
  if (editing) {
    return (
      <InlineRename
        value={label}
        label={renameLabel}
        onCancel={onCancelEdit}
        onSubmit={onRename}
      />
    );
  }

  return (
    <div className="group flex items-stretch gap-0.5">
      <button
        type="button"
        disabled={disabled}
        aria-current={selected ? "true" : undefined}
        aria-expanded={expanded}
        onClick={onSelect}
        // Selected is a colour, not a shade: "which patent am I in" is the question this
        // tree exists to answer, and the left bar carries it at the edge of vision.
        className={`focus-ring min-w-0 flex-1 rounded-lg py-1.5 pr-2 text-left transition-all duration-200 disabled:opacity-50 ${
          selected
            ? "border border-sky-300 border-l-[3px] border-l-sky-500 bg-gradient-to-r from-sky-50 to-white pl-[calc(0.5rem-2px)] shadow-sm"
            : "border border-transparent pl-2 hover:bg-slate-100 hover:shadow-sm"
        }`}
      >
        <span className="flex min-w-0 items-center gap-1.5">
          <span
            className={`min-w-0 flex-1 truncate text-[0.8125rem] leading-5 ${
              selected ? "font-semibold text-sky-900" : "text-slate-700"
            }`}
          >
            {label}
          </span>{" "}
          {badge}
        </span>{" "}
        <span
          className={`block truncate text-[0.6875rem] leading-4 ${
            selected ? "text-sky-700" : "text-slate-500"
          }`}
        >
          {meta}
        </span>
      </button>
      {/* Dimmed at rest, not hidden until hover: hover does not exist on touch. */}
      <button
        type="button"
        aria-label={renameLabel}
        onClick={onEdit}
        className="focus-ring flex w-6 shrink-0 items-center justify-center rounded-md text-slate-700 opacity-60 transition-opacity duration-150 hover:bg-slate-200/60 hover:text-slate-700 focus-visible:opacity-100 group-hover:opacity-100 max-lg:opacity-100"
      >
        {/* Pencil. aria-hidden because the button already has a name. */}
        <svg aria-hidden="true" viewBox="0 0 20 20" fill="currentColor" className="h-3.5 w-3.5">
          <path d="M13.6 2.6a1.9 1.9 0 0 1 2.7 2.7l-.8.8-2.7-2.7.8-.8ZM11.7 4.5l2.7 2.7-7 7H4.7v-2.7l7-7Z" />
        </svg>
      </button>
      {/* Same language as the pencil, in red. Present only when the caller passes
          onDelete. */}
      {onDelete && (
        <button
          type="button"
          aria-label={deleteLabel}
          disabled={disabled}
          onClick={onDelete}
          className="focus-ring flex w-6 shrink-0 items-center justify-center rounded-md text-slate-700 opacity-60 transition-opacity duration-150 hover:bg-red-50 hover:text-red-700 focus-visible:opacity-100 group-hover:opacity-100 max-lg:opacity-100 disabled:opacity-30"
        >
          {/* Trash can. aria-hidden because the button already has a name. */}
          <svg aria-hidden="true" viewBox="0 0 20 20" fill="currentColor" className="h-3.5 w-3.5">
            <path
              fillRule="evenodd"
              d="M8 3a1 1 0 0 0-1 1v.5H4.5a1 1 0 0 0 0 2H5l.6 9.1A2 2 0 0 0 7.6 17.4h4.8a2 2 0 0 0 2-1.8L15 5.5h.5a1 1 0 1 0 0-2H13V4a1 1 0 0 0-1-1H8Zm-.2 5.8a.7.7 0 0 1 1.4 0v5.4a.7.7 0 0 1-1.4 0V8.8Zm3.4-.7a.7.7 0 0 0-.7.7v5.4a.7.7 0 0 0 1.4 0V8.8a.7.7 0 0 0-.7-.7Z"
              clipRule="evenodd"
            />
          </svg>
        </button>
      )}
    </div>
  );
}
