import type { ReactNode } from "react";

import InlineRename from "./InlineRename";

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
  onSelect(): void;
  onEdit(): void;
  onCancelEdit(): void;
  /** Resolves to an error sentence to show under the field, or null on success. */
  onRename(value: string): Promise<string | null>;
}

/**
 * One row shape for both levels of the tree. Patents and versions differ only in
 * their text and their indent, and keeping two near-identical components meant
 * two copies of the same pencil SVG drifting apart.
 *
 * Props only, like every leaf here: a row that read the store could not be
 * rendered in a test without it.
 */
export default function TreeRow({
  label,
  meta,
  badge,
  selected,
  disabled,
  renameLabel,
  editing,
  expanded,
  onSelect,
  onEdit,
  onCancelEdit,
  onRename,
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
        className={`focus-ring min-w-0 flex-1 rounded-md px-2 py-1.5 text-left transition-colors duration-150 disabled:opacity-50 ${
          selected
            ? "border border-slate-200 bg-white shadow-sm"
            : "border border-transparent hover:bg-slate-200/60"
        }`}
      >
        <span className="flex min-w-0 items-center gap-1.5">
          <span
            className={`min-w-0 flex-1 truncate text-[0.8125rem] leading-5 ${
              selected ? "font-semibold text-slate-900" : "text-slate-700"
            }`}
          >
            {label}
          </span>{" "}
          {badge}
        </span>{" "}
        <span className="block truncate text-[0.6875rem] leading-4 text-slate-500">{meta}</span>
      </button>
      {/* opacity-60 at rest rather than hidden-until-hover: an affordance nobody
          can see is one nobody uses, and hover does not exist on touch. */}
      <button
        type="button"
        aria-label={renameLabel}
        onClick={onEdit}
        className="focus-ring flex w-6 shrink-0 items-center justify-center rounded-md text-slate-400 opacity-60 transition-opacity duration-150 hover:bg-slate-200/60 hover:text-slate-700 focus-visible:opacity-100 group-hover:opacity-100 max-lg:opacity-100"
      >
        {/* Pencil. aria-hidden because the button already has a name. */}
        <svg aria-hidden="true" viewBox="0 0 20 20" fill="currentColor" className="h-3.5 w-3.5">
          <path d="M13.6 2.6a1.9 1.9 0 0 1 2.7 2.7l-.8.8-2.7-2.7.8-.8ZM11.7 4.5l2.7 2.7-7 7H4.7v-2.7l7-7Z" />
        </svg>
      </button>
    </div>
  );
}
