import type { ReactNode } from "react";

export interface ComposerProps {
  value: string;
  onChange(value: string): void;
  onSend(): void;
  sending: boolean;
  /** Sticky once a 503 has been seen. The composer stays enabled: retry is one click. */
  aiUnavailable: boolean;
  /** The context chips and the .txt drop zone, rendered above the textarea. */
  children: ReactNode;
  /** Shown only when a file is attached and the composer is empty. */
  suggestions: string[];
  onSuggestion(text: string): void;
}

export default function Composer({
  value,
  onChange,
  onSend,
  sending,
  aiUnavailable,
  children,
  suggestions,
  onSuggestion,
}: ComposerProps) {
  return (
    <div className="border-t border-slate-200 p-3">
      {aiUnavailable && (
        <p className="mb-2 rounded-md bg-amber-50 px-2 py-1 text-[0.75rem] text-amber-900 ring-1 ring-amber-200">
          AI is unavailable in this environment. Versioning and manual editing are unaffected.
        </p>
      )}

      <div className="mb-2 flex flex-col gap-2">{children}</div>

      {suggestions.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1">
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => onSuggestion(suggestion)}
              className="focus-ring rounded-full border border-slate-300 px-2 py-0.5 text-[0.75rem] text-slate-600 hover:bg-slate-100"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      <textarea
        aria-label="Ask the AI"
        rows={3}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        // Enter sends, Shift+Enter newlines — the convention every chat UI uses,
        // and the reason the send button is not the only way out.
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            onSend();
          }
        }}
        placeholder="Ask for an edit, or a question…"
        className="focus-ring max-h-40 w-full resize-y rounded-lg border border-slate-300 px-2 py-1.5 text-[0.8125rem]"
      />

      <button
        type="button"
        onClick={onSend}
        // An empty composer cannot send, so an attached file can never be
        // interpreted as an instruction on its own.
        disabled={sending || value.trim() === ""}
        className="btn btn-primary focus-ring mt-2 w-full"
      >
        {sending ? "Thinking…" : "Send"}
      </button>
    </div>
  );
}
