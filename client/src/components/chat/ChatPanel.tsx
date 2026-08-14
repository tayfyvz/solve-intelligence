import { useEffect, useRef, useState } from "react";
import type { Editor } from "@tiptap/core";

import { localFormat } from "../../ai/format";
import { paint } from "../../ai/highlight";
import { scrollToClaim } from "../../ai/navigate";
import { buildSelectionContext, subscribeToSelection } from "../../ai/selection";
import { ApiError, RETRYABLE_STATUSES, aiApply, aiChat, toMessage } from "../../api";
import type { TextFile } from "../../textFile";
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
  /**
   * Who created the OPEN version, per the server's stored `source` field. "ai" means the
   * user already reviewed and confirmed one AI edit to arrive at this exact version, so
   * every further AI edit on it applies straight in rather than proposing again — and,
   * because this is read from the store rather than kept in local state, that stays true
   * across a reload or remount, not just for the rest of this browser session.
   */
  const versionOrigin = useDocumentStore((s) => s.versionOrigin);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [file, setFile] = useState<TextFile | null>(null);
  const [range, setRange] = useState<{ from: number; to: number } | null>(null);
  const [sending, setSending] = useState(false);
  /** Sticky: set by a 503, cleared never. The composer explains itself without another round-trip. */
  const [aiUnavailable, setAiUnavailable] = useState(false);
  /** The clarification loop. Both reset on any non-clarification outcome. */
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [clarifyCount, setClarifyCount] = useState(0);

  const nextId = useRef(1);
  const alive = useRef(true);
  /** Idempotency for Apply. A REF, not state: React state does not update until the next
   *  render, so two clicks inside one frame would both read `false`. */
  const applying = useRef(false);
  /** True from just before the aiChat await until the finally. A REF, not `sending`,
   *  because the version-change effect reads it from a closure with deps [versionNumber]
   *  and would otherwise see a stale value — and because it doubles as send()'s
   *  idempotency guard: React re-renders the disabled Send button too late, so four
   *  clicks inside one frame all read `sending === false` and fire four requests. */
  const inFlight = useRef(false);
  const citationTimer = useRef<number | null>(null);
  const lastInstruction = useRef("");
  const previousVersion = useRef(versionNumber);
  /**
   * The one thing "undo" can act on: the HTML the editor held immediately before the most
   * recent AI-applied change, and the version it belongs to. Session-only, single-step —
   * there is no persisted AI audit trail (DESIGN.md future work) and the six ops in
   * operations.py have no inverse, so a real undo stack would need new DB plumbing this
   * feature does not have. Cleared after one use; a second "undo" in a row falls through to
   * the explanatory message rather than pretending there is more history to walk back.
   */
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

  /** At most one live proposal. The older one was written against text the newer request has
   *  already moved past, and two Proceed buttons on screen is an ambiguity the user should not
   *  have to resolve. Nothing can go WRONG without this — the server's TTL and digest check
   *  both fail an old proposal closed — but the resulting 409 reads as a bug. */
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

  /**
   * The ONLY place in the client that calls `setContent`, and the only place that clears
   * the held selection. Its boolean return is honoured by both callers.
   */
  function applyHtml(ed: Editor, html: string): boolean {
    try {
      // POSITIONAL emitUpdate — @tiptap/core 2.x is setContent(content, emitUpdate?, ...).
      // `true` routes this through Editor.onUpdate, the single writer of `dirty`. DROPPING
      // THIS ARGUMENT is the single highest-severity one-character bug in the feature: the
      // flag stays false while the document has changed, and the user is told their work is
      // saved when it is not.
      ed.commands.setContent(html, true);
    } catch {
      // setContent can throw SYNCHRONOUSLY on malformed content. The throw happens during
      // parse, before dispatch, so no transaction and no onUpdate. THIS PUSHES THE ONLY
      // BUBBLE THE USER SHOULD SEE — every caller must therefore return on false.
      pushAssistant({
        tone: "error",
        text: "The AI returned content the editor could not apply. The document was not changed.",
      });
      return false;
    }
    // The held range indexed into the OLD document. Cleared HERE, in the same block as
    // setContent, because a range resolved against the new document points at arbitrary text.
    setRange(null);
    paint(ed, { kind: "clear" });
    return true;
  }

  async function send(text?: string): Promise<void> {
    const instruction = (text ?? input).trim();
    // `inFlight` first: it is the ref, updated synchronously, and it is what stops
    // the second, third and fourth click of the same frame. `sending` is kept as
    // the readable state check for everything after the first render.
    if (!instruction || inFlight.current || sending) return;

    const ed = useDocumentStore.getState().editor;
    if (!ed || ed.isDestroyed) {
      pushAssistant({ tone: "error", text: "There is no open document to edit." });
      return;
    }

    // Narrowed ONCE, here, instead of `documentId!` at the call site. These two consts are
    // also what the staleness guards compare against.
    const docId = documentId;
    const verNum = versionNumber;
    if (docId === null || verNum === null) {
      pushAssistant({ tone: "error", text: "There is no open document to edit." });
      return;
    }

    // ── the client-side undo fast-path ────────────────────────────────────────────────
    // Never leaves the browser, and runs before anything else touches the instruction: an
    // undo request is not something to plan, it is something to look up.
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

    // AT MOST ONE LIVE PROPOSAL — and this runs BEFORE the format fast-path, not after.
    // The fast-path mutates the document with setMark, which invalidates any open
    // proposal's base_sha256 while leaving its Apply button on screen; clicking it then
    // 409s, which is the exact bug supersede exists to prevent.
    supersedeOpenProposals();

    // ── the client-side format fast-path ──────────────────────────────────────────────
    // Runs only when a selection is held. Never leaves the browser.
    const held = range;
    const format = held ? localFormat(instruction) : null;
    if (format && held) {
      // Re-set the selection explicitly: the held range may be older than
      // editor.state.selection (the "only upgrade" rule), and NO .focus() — focus stays
      // in the composer. setMark/unsetMark, never toggleMark: "make it italic" is an
      // assertion, not a toggle, so asking twice must be idempotent.
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

    // CAPTURED ONCE. This is the reference point for the drift guard below, and the exact
    // bytes the server hashes into proposal.base_sha256. It must be editor.getHTML(),
    // NEVER store.content — the stored content has been through nh3 and getHTML() has been
    // through TipTap's normalisation; hashing two different normalisations of the same
    // document makes every proposal 409.
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
        consented, // THE PROMPT DECISION
        pending_question: pendingQuestion,
        clarify_count: clarifyCount,
      });

      // THREE INDEPENDENT STALENESS GUARDS. Three things move independently and the store's
      // request token covers none of them, because no store action was involved.
      if (!alive.current) return; // 1. the panel unmounted
      const s = useDocumentStore.getState();
      if (s.documentId !== docId || s.versionNumber !== verNum) return; // 2. navigated
      if (!s.editor || s.editor.isDestroyed) return; // 3. editor swapped mid-flight

      // The clarification loop's two scalars, updated on EVERY outcome so the loop cannot
      // ratchet: any non-clarification answer resets the count to 0.
      if (res.status === "needs_clarification") {
        setPendingQuestion(res.message);
        setClarifyCount((n) => n + 1);
      } else {
        setPendingQuestion(null);
        setClarifyCount(0);
      }

      if (res.status === "proposal" && res.proposal) {
        // Cross-check the echoed identifiers as well as the store: two versions with
        // byte-identical content would slip past the server's digest.
        if (res.proposal.document_id !== docId || res.proposal.version_number !== verNum) return;
        pushAssistant({
          text: res.message,
          warnings: res.warnings,
          citations: res.citations,
          proposal: res.proposal,
        });
        return; // nothing applied, nothing saved
      }

      // INVARIANT 3, as an early return so it is impossible to miss. Every status other than
      // "applied" — answer, needs_clarification, no_change, error, and a malformed "applied"
      // with a null html — leaves the document byte-identical.
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

      // ── THE DRIFT GUARD ───────────────────────────────────────────────────────────────
      // The consented path skips POST /api/ai/apply, so it skips the server's base_sha256
      // check too. THIS IS THAT CHECK. Without it, every keystroke typed during a 1.5-30 s
      // call is silently destroyed by setContent — and `dirty` is true either way, so the
      // Banner cannot warn the user and the lost text is not anywhere they will find it.
      //
      // String equality, not a hash: getHTML() is a stable single-line normalisation, and
      // one comparison of two ~40 kB strings is free next to the call we just made.
      //
      // The IDENTICAL guard exists in confirmProposal. Two call sites, one rule.
      if (s.editor.getHTML() !== sentHtml) {
        pushAssistant({ tone: "error", text: DRIFT_MESSAGE });
        return; // document byte-identical; consent unchanged
      }

      // THE RETURN VALUE IS NOT OPTIONAL. applyHtml returns false when setContent throws,
      // having already pushed its own error bubble. Ignoring it pushed a success claim
      // immediately underneath that error, with the document unchanged.
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
      // 429/502/504 are transient, so the bubble carries a Retry control that re-sends the
      // same instruction unchanged. Everything else gets a plain bubble — retrying a 413 or
      // a 422 cannot succeed, and offering it would be theatre.
      const status = error instanceof ApiError ? error.status : null;
      pushAssistant({
        tone: "error",
        text: toMessage(error),
        retry: status !== null && RETRYABLE_STATUSES.has(status),
      });
    } finally {
      // Every exit path including the stale returns: no later request exists to clear it,
      // and a stuck `sending` disables the composer for the rest of the session.
      inFlight.current = false;
      if (alive.current) setSending(false);
    }
  }

  async function confirmProposal(messageId: number, proposal: AiProposal): Promise<void> {
    // DOUBLE-CLICK IDEMPOTENCY. A ref, updated synchronously, so the second click inside the
    // same frame loses. `sending` is React state and would not: both clicks read false.
    //
    // NOTE it stays true across the VERSION SAVE too, not just the network call, so a second
    // click cannot race the save either.
    if (applying.current) return;
    applying.current = true;
    setSending(true);
    try {
      // Both of these used to return in silence: the user clicked "Apply these
      // changes" and literally nothing happened — no bubble, and the button still
      // on screen offering to do it again. Every other exit from this function
      // says something, and so do these.
      const ed = useDocumentStore.getState().editor;
      const docId = documentId;
      const verNum = versionNumber;
      if (!ed || ed.isDestroyed || docId === null || verNum === null) {
        resolveProposal(messageId, "failed");
        pushAssistant({ tone: "error", text: "There is no open document to edit." });
        return;
      }

      // CAPTURED ONCE, before the await — exactly as send() does, and for exactly the same
      // reason. This is the LIVE buffer re-read now (the user may have typed since the
      // proposal arrived), it is what the server hashes against proposal.base_sha256, and
      // it is the reference point for the drift guard below.
      const sentHtml = ed.getHTML();
      const res = await aiApply({ html: sentHtml, proposal });

      if (!alive.current) return;
      const s = useDocumentStore.getState();
      // These guards MUST run before setContent. Applying AI HTML computed for version 3
      // into version 1's editor is the worst outcome in this entire feature.
      if (s.documentId !== docId || s.versionNumber !== verNum) return;
      if (!s.editor || s.editor.isDestroyed) return;

      if (res.status !== "applied" || res.html === null) {
        resolveProposal(messageId, "failed");
        pushAssistant({ tone: "error", text: res.message, warnings: res.warnings });
        return; // document byte-identical; invariant 3 again
      }

      // The server's base_sha256 check covers everything typed BEFORE this request left. It
      // cannot cover what the user typed DURING it — /apply is fast but it is not instant,
      // and setContent would overwrite those keystrokes with no warning and no trace.
      if (s.editor.getHTML() !== sentHtml) {
        resolveProposal(messageId, "failed");
        pushAssistant({ tone: "error", text: DRIFT_MESSAGE });
        return; // nothing applied, nothing saved, no consent
      }

      if (!applyHtml(s.editor, res.html)) {
        resolveProposal(messageId, "failed");
        return;
      }

      // Captured ONCE into a local: between here and the save the user can type, and folding
      // those keystrokes into the version would put content in it that the AI never produced
      // and the user never reviewed as part of this change.
      const appliedHtml = res.html;

      // The version-name index is unique per patent, so asking "delete claim 3" twice in one
      // patent would otherwise fail the SECOND save for a reason that has nothing to do with
      // the edit. The server's default name is generated from the names already taken and can
      // NEVER collide, so falling back to it is always safe.
      const name = `AI: ${lastInstruction.current.slice(0, 48)}`;
      let ok = await saveAsNewVersion(name, { source: "ai", content: appliedHtml });
      if (!ok && useDocumentStore.getState().error?.includes("already")) {
        ok = await saveAsNewVersion(undefined, { source: "ai", content: appliedHtml });
      }

      if (!alive.current) return;
      const after = useDocumentStore.getState();

      // THE SUPERSEDED-SAVE DISCRIMINATOR. The store reports a discarded write as `false`
      // WITH `error === null` — it returns before any set() — and a genuine failure as
      // `false` WITH a sentence in `error`. It is NOT a navigation check: a real 413 that
      // happens to coincide with the user switching version is a genuine failure whose
      // bubble a navigation check would swallow.
      if (!ok && after.error === null) return; // superseded: silence is correct

      if (!ok) {
        // consent is deliberately NOT granted: no restore point exists.
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

      // THE STORE'S DRIFT GUARD FIRED. A created version always has a HIGHER number
      // than the one we were on, so "unchanged" means exactly one thing: the user typed
      // while the version was saving, so the store kept them here with their keystrokes
      // rather than remounting the editor and destroying them. The version exists; we
      // are not on it, so we must not claim to be, and consent belongs to the version
      // the user is looking at — which is not the reviewed one.
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

      // after.versionNumber is non-null here: saveAsNewVersion returned true, which is only
      // reachable from the branch that set it. Narrowed rather than asserted.
      // Consent itself needs no action here: saveAsNewVersion already set the store's
      // `versionOrigin` to "ai" atomically with `versionNumber`, so `consented` is already
      // true by the time this component re-renders.
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
      // A 409 from /apply is NOT retryable — the digest is stale, so the honest affordance
      // is to ask again, which is why `retry` is false here for every status.
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

  /** Scroll to a cited claim and flash it. Never focuses, never dispatches a document
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

  // NOTE: the highlight plugin is registered by `Editor.tsx`, not here. It moved when the
  // find bar became a second consumer — this panel is unmounted whenever the chat column
  // is collapsed, and a decoration layer that disappears with it is no use to anyone else.
  // This component still owns the citation flash; it just calls `paint()`.

  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    return subscribeToSelection(editor, setRange);
  }, [editor]);

  useEffect(() => {
    if (versionNumber === previousVersion.current) return;
    previousVersion.current = versionNumber;
    // getState(), not a subscription: this is a fact ABOUT the transition, read once at the
    // moment of the transition. Subscribing would re-render the panel for a value it never
    // displays.
    if (useDocumentStore.getState().versionSource === "ai") return; // our own save; keep it all

    // THE SILENT-LOSS CASE. An AI call must never disable Save, so the user can click "Save
    // as new version" during a 20 s call. That moves versionNumber, so the in-flight response
    // will hit staleness guard 2 and return without a word — and this effect is about to
    // delete the instruction they typed. They waited 20 seconds and would receive nothing,
    // not even their own question. One bubble, in the FRESH transcript, is the whole fix.
    setMessages(inFlight.current ? [build("assistant", { tone: "system", text: DISCARDED_MESSAGE })] : []);
    // No consent state to clear here: `consented` is derived from the store's
    // `versionOrigin`, which this same navigation already updated (selectVersion/
    // selectDocument set it from the freshly fetched version's `source`).
    setPendingQuestion(null);
    setClarifyCount(0);
    setRange(null);
    // NOTE what is deliberately NOT here: `applying.current = false`. confirmProposal's own
    // `finally` is the single owner of that ref, and it may still be awaiting the /apply round
    // trip right now — clearing it here would re-arm the double-click guard mid-flight.
    // The file chip also deliberately survives: the attached prior art is about the PATENT,
    // not the version.
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
