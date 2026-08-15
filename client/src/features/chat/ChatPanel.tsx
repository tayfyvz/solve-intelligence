import { useEffect, useRef, useState } from "react";
import type { Editor } from "@tiptap/core";

import { localFormat } from "./format";
import { paint } from "../editor/highlight";
import { scrollToClaim } from "../editor/navigate";
import { buildSelectionContext, subscribeToSelection } from "./selection";
import { ApiError, RETRYABLE_STATUSES, aiApply, aiChat, toMessage } from "../../services/api";
import type { TextFile } from "../../utils/textFile";
import { useDocumentStore } from "../../store";
import type { AiProposal, ChatTurn } from "../../types";
import Composer from "./Composer";
import ContextChips from "./ContextChips";
import type { ChatMessage } from "./Message";
import MessageList from "./MessageList";

export interface ChatPanelProps {
  documentId: number | null;
  versionNumber: number | null;
}

const DRIFT_MESSAGE =
  "You edited the document while the AI was working, so the change was not applied. " +
  "Ask again and it will use your current text.";

const DISCARDED_MESSAGE =
  "You saved a new version while the AI was working, so that request was discarded. " +
  "Ask again and it will use the new version.";

const FILE_SUGGESTIONS = [
  "summarise this file",
  "does it overlap with claim 1?",
  "add a claim based on it",
];

/** How long a clicked citation stays highlighted. */
const CITATION_MS = 2_500;

/** Leading "undo"/"revert" only — matches the chat instruction, not prose that mentions it. */
const UNDO_RE = /^\s*(undo|revert)\b/i;

export default function ChatPanel({ documentId, versionNumber }: ChatPanelProps) {
  const editor = useDocumentStore((s) => s.editor);
  const saveAsNewVersion = useDocumentStore((s) => s.saveAsNewVersion);
  /** Who created the open version. "ai" means the user already confirmed one AI edit to get
   *  here, so further edits apply straight in. Read from the store, so it survives a reload. */
  const versionOrigin = useDocumentStore((s) => s.versionOrigin);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [file, setFile] = useState<TextFile | null>(null);
  const [range, setRange] = useState<{ from: number; to: number } | null>(null);
  const [sending, setSending] = useState(false);
  /** Sticky: set by a 503, never cleared. The composer then explains itself with no round-trip. */
  const [aiUnavailable, setAiUnavailable] = useState(false);
  /** The clarification loop. Both reset on any non-clarification outcome. */
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [clarifyCount, setClarifyCount] = useState(0);

  const nextId = useRef(1);
  const alive = useRef(true);
  /** Refs, not state: state does not update until the next render, so several clicks inside
   *  one frame would all read the stale value and fire several requests. `inFlight` is also
   *  read from the version-change effect's closure, which `sending` would stale out. */
  const applying = useRef(false);
  const inFlight = useRef(false);
  const citationTimer = useRef<number | null>(null);
  const lastInstruction = useRef("");
  const previousVersion = useRef(versionNumber);
  /** What "undo" acts on: the HTML held just before the last AI change. Session-only and
   *  single-step — the operations have no inverse, so a real stack needs server plumbing. */
  const lastApplied = useRef<{ html: string; versionNumber: number } | null>(null);

  const consented = documentId !== null && versionNumber !== null && versionOrigin === "ai";

  function build(role: ChatMessage["role"], init: Partial<ChatMessage> & { text: string }) {
    return {
      id: nextId.current++,
      role,
      tone: "normal" as const,
      warnings: [],
      citations: [],
      options: [],
      proposal: null,
      resolution: null,
      retry: false,
      ...init,
    };
  }

  const pushUser = (text: string) => setMessages((ms) => [...ms, build("user", { text })]);

  const pushAssistant = (init: Partial<ChatMessage> & { text: string }) =>
    setMessages((ms) => [...ms, build("assistant", init)]);

  /** At most one live proposal: an older one was written against text the newer request has
   *  moved past, and its Apply button would 409 rather than fail visibly. */
  function supersedeOpenProposals(): void {
    setMessages((ms) =>
      ms.map((m) => (m.proposal ? { ...m, proposal: null, resolution: "superseded" } : m)),
    );
  }

  function resolveProposal(messageId: number, resolution: ChatMessage["resolution"]): void {
    setMessages((ms) =>
      ms.map((m) => (m.id === messageId ? { ...m, proposal: null, resolution } : m)),
    );
  }

  /** The only place in the client that calls `setContent`, and the only place that clears the
   *  held selection. Both callers must honour the boolean: `false` means an error bubble was
   *  already pushed and the document is unchanged. */
  function applyHtml(ed: Editor, html: string): boolean {
    try {
      // emitUpdate is POSITIONAL in @tiptap/core 2.x. `true` routes this through
      // Editor.onUpdate, the single writer of `dirty`; without it the document changes
      // while the app still reports it saved.
      ed.commands.setContent(html, true);
    } catch {
      // setContent throws synchronously on malformed content — during parse, so no
      // transaction and no onUpdate.
      pushAssistant({
        tone: "error",
        text: "The AI returned content the editor could not apply. The document was not changed.",
      });
      return false;
    }
    // The held range indexed into the OLD document, so it is cleared in the same block that
    // replaced it — against the new one it points at arbitrary text.
    setRange(null);
    paint(ed, { kind: "clear" });
    return true;
  }

  async function send(text?: string): Promise<void> {
    const instruction = (text ?? input).trim();
    // `inFlight` first: the ref is what stops the second click of the same frame.
    if (!instruction || inFlight.current || sending) return;

    const ed = useDocumentStore.getState().editor;
    if (!ed || ed.isDestroyed) {
      pushAssistant({ tone: "error", text: "There is no open document to edit." });
      return;
    }

    // Narrowed once, here: these are also what the staleness guards compare against.
    const docId = documentId;
    const verNum = versionNumber;
    if (docId === null || verNum === null) {
      pushAssistant({ tone: "error", text: "There is no open document to edit." });
      return;
    }

    // Undo fast-path: a lookup, not something to plan. Never leaves the browser.
    if (UNDO_RE.test(instruction)) {
      pushUser(instruction);
      setInput("");
      const target = lastApplied.current;
      if (target && target.versionNumber === verNum) {
        if (!applyHtml(ed, target.html)) return;
        lastApplied.current = null; // single-step: nothing further back to walk to
        pushAssistant({
          tone: "system",
          text: "Reverted your last AI change. Not saved yet — use Save in the top bar to keep this, or make another change.",
        });
      } else {
        pushAssistant({
          tone: "system",
          text:
            "I can only undo the most recent AI change from this session, and there isn't one " +
            "tracked for this version. Cmd+Z (or the Undo button in the toolbar) still works if " +
            "you haven't reloaded, or switch versions in the top bar if the change was already saved.",
        });
      }
      return;
    }

    // Before the format fast-path, not after: setMark invalidates an open proposal's digest
    // while leaving its Apply button on screen.
    supersedeOpenProposals();

    // Format fast-path: only with a selection held, and never leaves the browser.
    const held = range;
    const format = held ? localFormat(instruction) : null;
    if (format && held) {
      // Re-set the selection (the held range may be older than editor.state.selection) and
      // no .focus() — focus stays in the composer. setMark/unsetMark rather than toggleMark:
      // "make it italic" is an assertion, so asking twice must be idempotent.
      const chain = ed.chain().setTextSelection(held);
      (format.on ? chain.setMark(format.mark) : chain.unsetMark(format.mark)).run();
      pushUser(instruction);
      pushAssistant({
        tone: "system",
        text: `Done — ${format.on ? "applied" : "removed"} ${format.mark} on the selected text. This one was handled in the editor, with no AI call.`,
      });
      setInput("");
      return;
    }

    // Captured once: the reference point for the drift guard, and the exact bytes the server
    // hashes into proposal.base_sha256. getHTML(), never store.content — the stored content
    // has been through nh3, and hashing two normalisations of one document 409s every time.
    const sentHtml = ed.getHTML();
    const selection = range ? buildSelectionContext(ed.state.doc, range) : null;
    const history: ChatTurn[] = messages
      .filter((m) => m.tone === "normal" || m.tone === "clarify")
      .slice(-6)
      .map((m) => ({ role: m.role, content: m.text }));

    lastInstruction.current = instruction;
    pushUser(instruction);
    setInput("");
    setSending(true);
    inFlight.current = true;

    try {
      const res = await aiChat({
        document_id: docId,
        version_number: verNum,
        html: sentHtml,
        instruction,
        context_text: file?.text ?? null, // re-sent every request; the chip is NOT cleared
        context_name: file?.name ?? null,
        selection,
        history,
        consented,
        pending_question: pendingQuestion,
        clarify_count: clarifyCount,
      });

      // Three things move independently of the store's request token, because no store
      // action was involved: the panel, the open version, and the editor instance.
      if (!alive.current) return;
      const s = useDocumentStore.getState();
      if (s.documentId !== docId || s.versionNumber !== verNum) return;
      if (!s.editor || s.editor.isDestroyed) return;

      // Updated on every outcome, so the clarification loop cannot ratchet.
      if (res.status === "needs_clarification") {
        setPendingQuestion(res.message);
        setClarifyCount((n) => n + 1);
      } else {
        setPendingQuestion(null);
        setClarifyCount(0);
      }

      if (res.status === "proposal" && res.proposal) {
        // Cross-check the echoed identifiers too: two versions with byte-identical content
        // would slip past the server's digest.
        if (res.proposal.document_id !== docId || res.proposal.version_number !== verNum) return;
        pushAssistant({
          text: res.message,
          warnings: res.warnings,
          citations: res.citations,
          proposal: res.proposal,
        });
        return; // nothing applied, nothing saved
      }

      // Every status other than "applied" leaves the document byte-identical.
      if (res.status !== "applied" || res.html === null) {
        pushAssistant({
          tone:
            res.status === "error"
              ? "error"
              : res.status === "needs_clarification"
                ? "clarify"
                : "normal",
          text: res.message,
          warnings: res.warnings,
          citations: res.citations,
          options: res.options,
        });
        return;
      }

      // The drift guard. The consented path skips POST /api/ai/apply and so skips the
      // server's digest check; this is that check. Without it, keystrokes typed during the
      // call are destroyed by setContent, and `dirty` is true either way so nothing warns.
      // String equality is enough — getHTML() is a stable normalisation.
      if (s.editor.getHTML() !== sentHtml) {
        pushAssistant({ tone: "error", text: DRIFT_MESSAGE });
        return;
      }

      if (!applyHtml(s.editor, res.html)) return;
      lastApplied.current = { html: sentHtml, versionNumber: verNum };

      pushAssistant({
        text: `${res.message}\n\nApplied to version ${verNum}. Not saved yet — use Save in the top bar when you are happy with it.`,
        warnings: res.warnings,
        citations: res.citations,
      });
    } catch (error) {
      if (!alive.current) return;
      if (error instanceof ApiError && error.status === 503) {
        setAiUnavailable(true);
        pushAssistant({
          tone: "error",
          text: "AI editing is unavailable — no OpenAI API key is configured. Versioning and manual editing work normally.",
        });
        return;
      }
      // Only the transient statuses offer Retry: re-sending a 413 or a 422 cannot succeed.
      const status = error instanceof ApiError ? error.status : null;
      pushAssistant({
        tone: "error",
        text: toMessage(error),
        retry: status !== null && RETRYABLE_STATUSES.has(status),
      });
    } finally {
      // Every exit path, including the stale returns: a stuck `sending` disables the
      // composer for the rest of the session.
      inFlight.current = false;
      if (alive.current) setSending(false);
    }
  }

  async function confirmProposal(messageId: number, proposal: AiProposal): Promise<void> {
    // Held across the version save as well as the network call, so a second click cannot
    // race the save either.
    if (applying.current) return;
    applying.current = true;
    setSending(true);
    try {
      const ed = useDocumentStore.getState().editor;
      const docId = documentId;
      const verNum = versionNumber;
      if (!ed || ed.isDestroyed || docId === null || verNum === null) {
        resolveProposal(messageId, "failed");
        pushAssistant({ tone: "error", text: "There is no open document to edit." });
        return;
      }

      // Re-read now — the user may have typed since the proposal arrived. This is what the
      // server hashes against proposal.base_sha256.
      const sentHtml = ed.getHTML();
      const res = await aiApply({ html: sentHtml, proposal });

      if (!alive.current) return;
      const s = useDocumentStore.getState();
      // Before setContent: applying HTML computed for version 3 into version 1's editor is
      // the worst outcome in this feature.
      if (s.documentId !== docId || s.versionNumber !== verNum) return;
      if (!s.editor || s.editor.isDestroyed) return;

      if (res.status !== "applied" || res.html === null) {
        resolveProposal(messageId, "failed");
        pushAssistant({ tone: "error", text: res.message, warnings: res.warnings });
        return;
      }

      // The server's digest covers what was typed before the request left, not during it.
      if (s.editor.getHTML() !== sentHtml) {
        resolveProposal(messageId, "failed");
        pushAssistant({ tone: "error", text: DRIFT_MESSAGE });
        return;
      }

      if (!applyHtml(s.editor, res.html)) {
        resolveProposal(messageId, "failed");
        return;
      }

      // Captured before the save: keystrokes typed in between must not be folded into a
      // version presented as the AI's reviewed output.
      const appliedHtml = res.html;

      // Version names are unique per patent, so the same instruction twice would otherwise
      // fail the second save. The server's generated default can never collide.
      const name = `AI: ${lastInstruction.current.slice(0, 48)}`;
      let ok = await saveAsNewVersion(name, { source: "ai", content: appliedHtml });
      if (!ok && useDocumentStore.getState().error?.includes("already")) {
        ok = await saveAsNewVersion(undefined, { source: "ai", content: appliedHtml });
      }

      if (!alive.current) return;
      const after = useDocumentStore.getState();

      // The store reports a superseded write as `false` with `error === null`, and a genuine
      // failure as `false` with a sentence in `error`. Not a navigation check: a real 413
      // that coincides with a version switch still deserves its bubble.
      if (!ok && after.error === null) return;

      if (!ok) {
        // Consent is deliberately not granted: no restore point exists.
        resolveProposal(messageId, "applied_unsaved");
        lastApplied.current = { html: sentHtml, versionNumber: verNum };
        pushAssistant({
          tone: "error",
          text: `The edit was applied but could not be saved: ${after.error} Your changes are still in the editor — use "Save as new version" in the top bar to keep them.`,
          warnings: res.warnings,
        });
        return;
      }

      resolveProposal(messageId, "applied");

      // A created version always has a higher number, so "unchanged" means the store's own
      // drift guard fired: the user typed while it saved, and was kept here with their
      // keystrokes. The version exists but we are not on it, so we must not claim to be.
      if (after.versionNumber === verNum) {
        pushAssistant({
          tone: "system",
          text:
            `The change was saved as a new version, but you typed while it was saving — so ` +
            `you are still on version ${verNum} with unsaved changes. Open the newest version ` +
            `in the sidebar to see the AI's copy.`,
          warnings: res.warnings,
        });
        return;
      }

      // Consent needs no action: saveAsNewVersion set `versionOrigin` atomically with
      // `versionNumber`, so `consented` is already true by the next render.
      const saved = after.versionNumber;
      if (saved !== null) {
        lastApplied.current = { html: sentHtml, versionNumber: saved };
      }
      pushAssistant({
        text: `${res.message}\n\nSaved as version ${saved}. Further AI changes will apply straight away — switch versions in the top bar to go back.`,
        warnings: res.warnings,
      });
    } catch (error) {
      if (!alive.current) return;
      resolveProposal(messageId, "failed");
      // A 409 from /apply is not retryable — the digest is stale, so the honest affordance
      // is to ask again.
      pushAssistant({ tone: "error", text: toMessage(error) });
    } finally {
      applying.current = false;
      if (alive.current) setSending(false);
    }
  }

  function cancelProposal(messageId: number): void {
    resolveProposal(messageId, "cancelled");
    pushAssistant({ tone: "system", text: "Cancelled — the document was not changed." });
  }

  /** Scroll to a cited claim and flash it. Never focuses and never dispatches a document
   *  transaction: the user is mid-conversation. */
  function showClaim(claimNumber: number): void {
    const ed = useDocumentStore.getState().editor;
    if (!ed || ed.isDestroyed) return;
    const span = scrollToClaim(ed, claimNumber);
    if (!span) return;
    paint(ed, { kind: "citation", ...span });
    if (citationTimer.current !== null) window.clearTimeout(citationTimer.current);
    citationTimer.current = window.setTimeout(() => {
      const live = useDocumentStore.getState().editor;
      if (live && !live.isDestroyed) paint(live, { kind: "clear" });
    }, CITATION_MS);
  }

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // A fired timer must not dispatch into a destroyed view.
  useEffect(
    () => () => {
      if (citationTimer.current !== null) window.clearTimeout(citationTimer.current);
    },
    [],
  );

  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    return subscribeToSelection(editor, setRange);
  }, [editor]);

  useEffect(() => {
    if (versionNumber === previousVersion.current) return;
    previousVersion.current = versionNumber;
    // getState(), not a subscription: a fact about the transition, read once. Subscribing
    // would re-render the panel for a value it never displays.
    if (useDocumentStore.getState().versionSource === "ai") return; // our own save; keep it all

    // Saving a new version mid-call moves versionNumber, so the in-flight response returns
    // silently at the staleness guard — and this effect is about to clear the transcript.
    // One bubble in the fresh transcript is the difference between that and nothing at all.
    setMessages(inFlight.current ? [build("assistant", { tone: "system", text: DISCARDED_MESSAGE })] : []);
    setPendingQuestion(null);
    setClarifyCount(0);
    setRange(null);
    // Deliberately absent: `applying.current = false` (confirmProposal's finally owns it and
    // may still be mid-flight) and clearing the file chip (the prior art is about the patent,
    // not the version).
  }, [versionNumber]);

  /** `Selection · claim 3` / `Selection · claims 3–5` / `Selection · 412 characters`. */
  function selectionLabel(): string | null {
    if (!range || !editor || editor.isDestroyed) return null;
    const context = buildSelectionContext(editor.state.doc, range);
    if (!context) return null;
    const claims = context.claim_numbers;
    if (claims.length === 1) return `Selection · claim ${claims[0]}`;
    if (claims.length > 1) return `Selection · claims ${claims[0]}–${claims[claims.length - 1]}`;
    return `Selection · ${context.text.length} characters`;
  }

  function hoverSelection(hovering: boolean): void {
    const ed = useDocumentStore.getState().editor;
    if (!ed || ed.isDestroyed || !range) return;
    paint(ed, hovering ? { kind: "selection", ...range } : { kind: "clear" });
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <MessageList
        messages={messages}
        sending={sending}
        onOption={(text) => void send(text)}
        onRetry={() => void send(lastInstruction.current)}
        onCitation={showClaim}
        onConfirm={(messageId, proposal) => void confirmProposal(messageId, proposal)}
        onCancel={cancelProposal}
        onExample={setInput}
      />

      <Composer
        value={input}
        onChange={setInput}
        onSend={() => void send()}
        sending={sending}
        aiUnavailable={aiUnavailable}
        suggestions={file && input.trim() === "" ? FILE_SUGGESTIONS : []}
        onSuggestion={(text) => void send(text)}
      >
        <ContextChips
          selectionLabel={selectionLabel()}
          onClearSelection={() => {
            setRange(null);
            hoverSelection(false);
          }}
          onHoverSelection={hoverSelection}
          file={file}
          onAttach={setFile}
          onReject={(message) => pushAssistant({ tone: "error", text: message })}
          onClearFile={() => setFile(null)}
          disabled={sending}
        />
      </Composer>
    </div>
  );
}
