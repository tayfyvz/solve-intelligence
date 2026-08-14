import type { AiProposal } from "../../types";

export interface ProposalPromptProps {
  proposal: AiProposal;
  /** A request is in flight: both buttons are inert. */
  sending: boolean;
  onConfirm(): void;
  onCancel(): void;
}

/**
 * The confirmation, inline in the transcript and never a modal: a modal would cover
 * the document the user needs to read in order to answer, which is the one thing
 * the confirmation exists for.
 *
 * It carries no HTML. The preview is the server's own per-operation summary lines
 * plus the text the operations would write — so what the user approves is what they
 * were shown, in the words the server used.
 */
export default function ProposalPrompt({
  proposal,
  sending,
  onConfirm,
  onCancel,
}: ProposalPromptProps) {
  return (
    // Tinted in the accent, unlike every other block in a bubble: this is the one
    // place in the transcript where a click changes the document, and it has to be
    // findable at a glance in a long scroll-back.
    <div className="mt-2 rounded-xl border border-indigo-200 bg-gradient-to-b from-indigo-50/80 to-white p-3 shadow-sm">
      {/* The single most useful thing an attorney can be told before clicking:
          does this write new claim language, or only rearrange text they wrote? */}
      {proposal.authors_new_text && (
        <p className="mb-2 text-[0.8125rem] font-medium text-slate-700">
          ✎ This writes new claim language.
        </p>
      )}

      <ul className="ml-4 list-disc space-y-1 text-[0.8125rem] text-slate-700">
        {proposal.summary.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>

      {/* The honest description of what Proceed does, and the answer to "why am I
          being asked?". */}
      <p className="mt-3 text-[0.8125rem] text-slate-600">
        Applying this will save a new version first, so your current version stays untouched.
      </p>

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          className="btn btn-primary focus-ring"
          onClick={onConfirm}
          disabled={sending}
        >
          Apply these changes
        </button>
        <button
          type="button"
          className="btn btn-secondary focus-ring"
          onClick={onCancel}
          disabled={sending}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
