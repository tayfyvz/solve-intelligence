import { useEffect, useLayoutEffect, useRef, useState } from "react";

import type { AiProposal } from "../../types";
import Message, { type ChatMessage } from "./Message";

export interface MessageListProps {
  messages: ChatMessage[];
  sending: boolean;
  onOption(text: string): void;
  onRetry(): void;
  onCitation(claimNumber: number): void;
  onConfirm(messageId: number, proposal: AiProposal): void;
  onCancel(messageId: number): void;
  /** The empty state's examples fill the composer; they do not send. */
  onExample(text: string): void;
}

/** Three of the README's own instructions, so the first thing a reviewer sees works. */
const EXAMPLES = [
  "Delete claim 3 and renumber the rest",
  "Make claim 1 bold",
  "Add a dependent claim after claim 2",
];

/**
 * There is no streaming (§1.1), so this is a fixed sequence rather than live node
 * progress: it says what the pipeline is doing at roughly the pace it does it, and
 * it holds on the last label rather than claiming to be finished.
 */
const STEPS = [
  "Reading your request…",
  "Finding the relevant claims…",
  "Drafting…",
  "Checking antecedent basis and claim form…",
  "Verifying…",
];

/** How close to the bottom counts as "following along". */
const STICK_PX = 80;

function Thinking() {
  const [step, setStep] = useState(0);
  useEffect(() => {
    const id = window.setInterval(
      () => setStep((n) => Math.min(n + 1, STEPS.length - 1)),
      2_000,
    );
    return () => window.clearInterval(id);
  }, []);

  return (
    <li className="flex justify-stretch">
      <div className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-[0.8125rem] text-slate-500">
        <span className="animate-pulse">● ● ●</span> {STEPS[step]}
      </div>
    </li>
  );
}

export default function MessageList({
  messages,
  sending,
  onOption,
  onRetry,
  onCitation,
  onConfirm,
  onCancel,
  onExample,
}: MessageListProps) {
  const container = useRef<HTMLDivElement>(null);
  /** Measured BEFORE the new message paints — after it, the old distance is gone. */
  const wasAtBottom = useRef(true);

  useLayoutEffect(() => {
    const element = container.current;
    if (!element) return;
    // Yanking the view away from a warning the user is reading is worse than a
    // missed scroll, so a user who has scrolled up is left where they are.
    if (wasAtBottom.current) element.scrollTop = element.scrollHeight;
  }, [messages.length, sending]);

  return (
    <div
      ref={container}
      data-testid="message-list"
      onScroll={(event) => {
        const element = event.currentTarget;
        wasAtBottom.current =
          element.scrollHeight - element.scrollTop - element.clientHeight <= STICK_PX;
      }}
      className="min-h-0 flex-1 overflow-y-auto px-3 py-3"
    >
      {messages.length === 0 && !sending ? (
        <div className="text-[0.8125rem] text-slate-500">
          <p>Ask for an edit, or ask a question about this patent.</p>
          <div className="mt-2 flex flex-col items-start gap-1">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => onExample(example)}
                className="focus-ring rounded-md border border-slate-200 px-2 py-1 text-left text-[0.75rem] text-slate-600 hover:bg-slate-100"
              >
                {example}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {messages.map((message, index) => (
            <Message
              key={message.id}
              message={message}
              isLast={index === messages.length - 1}
              sending={sending}
              onOption={onOption}
              onRetry={onRetry}
              onCitation={onCitation}
              onConfirm={onConfirm}
              onCancel={onCancel}
            />
          ))}
          {sending && <Thinking />}
        </ul>
      )}
    </div>
  );
}
