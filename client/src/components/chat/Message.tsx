import type { AiProposal } from "../../types";
import ProposalPrompt from "./ProposalPrompt";

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  tone: "normal" | "clarify" | "error" | "system";
  text: string;
  warnings: string[];
  citations: number[];
  /** Complete prefilled instructions; clicking one calls send(text). Rendered ONLY on the
   *  last assistant message, so a stale question's options are not clickable three turns on. */
  options: string[];
  /** Set only on the bubble that carries a live proposal; cleared once resolved. */
  proposal: AiProposal | null;
  resolution: "applied" | "applied_unsaved" | "cancelled" | "failed" | "superseded" | null;
  /** Set on error bubbles whose failure was transient (429/502/504). Renders a Retry
   *  control on the LAST message only, which re-sends the same instruction unchanged. */
  retry: boolean;
}

export interface MessageProps {
  message: ChatMessage;
  /** Options, Retry and a live proposal only ever act on the newest message. */
  isLast: boolean;
  sending: boolean;
  onOption(text: string): void;
  onRetry(): void;
  onCitation(claimNumber: number): void;
  onConfirm(messageId: number, proposal: AiProposal): void;
  onCancel(messageId: number): void;
}

/** What a resolved proposal bubble says once its buttons are gone. */
const RESOLUTIONS: Record<NonNullable<ChatMessage["resolution"]>, string> = {
  applied: "Applied",
  // Accurate about the *edit* without reading as "everything worked".
  applied_unsaved: "Applied — not saved",
  cancelled: "Cancelled",
  failed: "Not applied",
  superseded: "Superseded — ask again if you still want this.",
};

// `shadow-sm` is per-tone, not on the base bubble: the system tone is a bare line
// of centred grey text with no border and no fill, and a shadow under nothing
// draws a rectangle the reader can see but not explain.
const TONES: Record<ChatMessage["tone"], string> = {
  normal: "border-slate-200 bg-white shadow-sm",
  clarify: "border-slate-200 border-l-4 border-l-indigo-400 bg-white shadow-sm",
  error: "border-red-200 border-l-4 border-l-red-500 bg-red-50/60 text-red-700 shadow-sm",
  system: "border-transparent bg-transparent text-center text-slate-500",
};

export default function Message({
  message,
  isLast,
  sending,
  onOption,
  onRetry,
  onCitation,
  onConfirm,
  onCancel,
}: MessageProps) {
  const mine = message.role === "user";
  const proposal = message.proposal;

  return (
    <li className={`msg-in flex ${mine ? "justify-end" : "justify-stretch"}`}>
      <div
        className={`min-w-0 rounded-xl border px-3 py-2 text-[0.8125rem] ${
          mine
            ? // Ink, like the primary button: the user's own turn is the one thing
              // in the transcript that never needs to be read twice, so it can be
              // the darkest object and still be the least demanding.
              "max-w-[85%] border-slate-800 bg-gradient-to-br from-slate-700 to-slate-900 text-white shadow-md shadow-slate-900/15"
            : "w-full"
        } ${mine ? "" : TONES[message.tone]}`}
      >
        {/* whitespace-pre-line: the bubbles deliberately carry a blank line between
            the server's sentence and ours ("… Not saved yet"). */}
        <p className="whitespace-pre-line break-words">{message.text}</p>

        {/* Warnings are the highest-value output the feature produces and the thing
            most likely to be scrolled past in a demo. Not a footnote. */}
        {message.warnings.length > 0 && (
          <div className="mt-2 rounded-md bg-amber-50 p-2 ring-1 ring-amber-200">
            <p className="text-[0.75rem] font-semibold text-amber-900">Warnings</p>
            <ul className="ml-4 list-disc text-[0.75rem] text-amber-900">
              {message.warnings.map((warning, index) => (
                <li key={index}>{warning}</li>
              ))}
            </ul>
          </div>
        )}

        {message.citations.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {message.citations.map((claimNumber) => (
              <button
                key={claimNumber}
                type="button"
                aria-label={`Show claim ${claimNumber}`}
                // The user is mid-conversation: focus must not leave the composer.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => onCitation(claimNumber)}
                className="focus-ring rounded-full border border-sky-200 bg-sky-50 px-2.5 py-0.5 text-[0.75rem] font-medium text-sky-800 transition-colors duration-150 hover:border-sky-300 hover:bg-sky-100"
              >
                Claim {claimNumber}
              </button>
            ))}
          </div>
        )}

        {proposal && (
          <ProposalPrompt
            proposal={proposal}
            sending={sending}
            onConfirm={() => onConfirm(message.id, proposal)}
            onCancel={() => onCancel(message.id)}
          />
        )}

        {message.resolution && (
          <p className="mt-2 text-[0.75rem] font-medium text-slate-500">
            {RESOLUTIONS[message.resolution]}
          </p>
        )}

        {/* Last message only: a stale question's options must not be clickable
            three turns later, and a Retry further up would re-send something the
            user has moved past. */}
        {isLast && message.options.length > 0 && (
          <div className="mt-2 flex flex-col items-start gap-1">
            {message.options.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => onOption(option)}
                disabled={sending}
                className="focus-ring rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-left text-[0.75rem] font-medium text-indigo-900 transition-colors duration-150 hover:border-indigo-300 hover:bg-indigo-100 disabled:opacity-50"
              >
                {option}
              </button>
            ))}
          </div>
        )}

        {isLast && message.retry && (
          <button
            type="button"
            className="btn btn-secondary focus-ring mt-2"
            onClick={onRetry}
            disabled={sending}
            onMouseDown={(event) => event.preventDefault()}
          >
            Retry
          </button>
        )}
      </div>
    </li>
  );
}
