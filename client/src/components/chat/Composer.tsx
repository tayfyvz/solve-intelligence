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

/**
 * The server's own cap (`INSTRUCTION_TOO_LONG`). Enforced here so a long paste
 * fails before the round trip instead of after it — the server's 422 is a good
 * sentence, but by the time it arrives the composer has been cleared and the
 * user's text is gone.
 */
const MAX_INSTRUCTION = 2_000;

/** Far enough from the cap to be a warning, close enough not to be noise. */
const COUNTER_FROM = MAX_INSTRUCTION - 200;

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
    // A tinted footer, so the composer reads as a fixed surface the transcript
    // scrolls behind rather than as the last item in the list.
    <div className="rounded-xl border border-slate-200 bg-gradient-to-b from-slate-50 to-white p-3 shadow-sm">
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
              className="focus-ring rounded-full border border-slate-300 bg-white px-2.5 py-0.5 text-[0.75rem] text-slate-600 transition-colors duration-150 hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-800"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      <textarea
        aria-label="Ask the AI"
        rows={3}
        maxLength={MAX_INSTRUCTION}
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
        className="focus-ring max-h-40 w-full resize-y rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-[0.8125rem] shadow-inner transition-colors duration-150 placeholder:text-slate-400 hover:border-slate-400"
      />

      {value.length >= COUNTER_FROM && (
        <p aria-live="polite" className="mt-1 text-right text-[0.75rem] text-slate-500">
          {value.length} / {MAX_INSTRUCTION} characters
        </p>
      )}

      <button
        type="button"
        onClick={onSend}
        // An empty composer cannot send, so an attached file can never be
        // interpreted as an instruction on its own.
        disabled={sending || value.trim() === ""}
        className="btn btn-accent focus-ring mt-2 w-full"
      >
        {sending ? "Thinking…" : "Send"}
      </button>
    </div>
  );
}
