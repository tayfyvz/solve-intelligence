import { useState } from "react";

import Modal from "../../components/ui/Modal";
import { SpinnerLabel } from "../../components/ui/Spinner";

export interface NewPatentDialogProps {
  /** Creates the patent. Resolves to an error sentence (a duplicate title 409s with a
   *  readable one) or null on success, in which case the parent closes the dialog. */
  onCreate(title: string, content: string): Promise<string | null>;
  onCancel(): void;
}

/** Title required, starting content optional. The error stays in the dialog with the fields
 *  filled in: closing it would throw away the content the user just pasted. */
export default function NewPatentDialog({ onCreate, onCancel }: NewPatentDialogProps) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Trimmed here as well as on the wire, so "   " cannot enable the button.
  const trimmed = title.trim();

  const submit = async () => {
    if (pending || !trimmed) return;
    setPending(true);
    setError(null);
    const message = await onCreate(trimmed, content.trim());
    setPending(false);
    if (message) setError(message);
  };

  return (
    <Modal
      labelledBy="new-patent-title"
      onDismiss={pending ? undefined : onCancel}
      className="max-w-[30rem] p-6"
    >
      <h2 id="new-patent-title" className="text-lg font-semibold tracking-tight">
        New patent
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-slate-600">
        Creates the patent and opens its first version.
      </p>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label htmlFor="new-patent-title-input" className="mt-5 block text-sm font-medium">
          Title
        </label>
        <input
          id="new-patent-title-input"
          autoFocus
          required
          // The server's limit, so a long paste is trimmed here rather than 422ing.
          maxLength={200}
          value={title}
          disabled={pending}
          onChange={(event) => setTitle(event.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "new-patent-error" : undefined}
          className="focus-ring mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:opacity-60"
        />

        <label htmlFor="new-patent-content-input" className="mt-4 block text-sm font-medium">
          Starting content <span className="font-normal text-slate-500">(optional)</span>
        </label>
        <textarea
          id="new-patent-content-input"
          rows={4}
          value={content}
          disabled={pending}
          onChange={(event) => setContent(event.target.value)}
          placeholder="<h1>Claims</h1><p>1. A device comprising…</p>"
          className="focus-ring mt-1 w-full resize-y rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs disabled:opacity-60"
        />

        {error && (
          <p
            id="new-patent-error"
            role="alert"
            className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100"
          >
            {error}
          </p>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            disabled={pending}
            onClick={onCancel}
            className="btn btn-secondary focus-ring"
          >
            Cancel
          </button>
          <button
            type="submit"
            aria-label="Create patent"
            disabled={pending || !trimmed}
            className="btn btn-primary focus-ring"
          >
            <SpinnerLabel spinning={pending}>Create patent</SpinnerLabel>
          </button>
        </div>
      </form>
    </Modal>
  );
}
