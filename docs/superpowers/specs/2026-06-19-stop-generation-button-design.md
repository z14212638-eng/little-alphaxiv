# Stop-generation button (arrow ↔ square)

**Date:** 2026-06-19
**Status:** Design (awaiting implementation plan)
**Author:** brainstormed with user

## Goal

Let the user abort an in-flight AI reply. While the assistant is responding, the
send button morphs from an up-arrow (➤, "send") into a square (■, "stop");
clicking it cancels the generation. When generation ends — either because the
model finished or the user clicked stop — the button returns to the arrow.

The user-chosen behavior for partial content: **keep what already streamed and
mark it "已停止" (stopped)**, with a low-key (non-red) marker — not the red
error tag currently used for network interruptions.

## Background — current state (verified against `main` @ 38d754e)

- The abort **plumbing already exists** but is unused:
  - `runConversation(opts)` accepts `signal?: AbortSignal` (`lib/llm.ts:76`)
    and forwards it to `streamChat`.
  - `streamChat` passes `signal` straight to `fetch` (`lib/api.ts:67`).
  - `parseSSE`'s `reader.read()` rejects on abort, so the `AbortError`
    propagates up through `streamChat` → `runConversation` → caller.
  - **But** `ChatPanel.send()` never creates an `AbortController` and never
    passes a `signal` to `runConversation` (`ChatPanel.tsx:259-305`).
- The send button is **not** inline in `ChatPanel` anymore. The chat-composer
  merge (38d754e) extracted the whole input row into `ChatComposer`
  (`components/ChatComposer.tsx`). The send button lives at `ChatComposer.tsx:139-148`:
  `className="composer-icon-btn composer-send-btn"`, glyph `➤`, `onClick={onSend}`,
  `disabled={!canSend}` where `canSend = !busy && (text or attachments)`.
- `ChatComposer` receives `busy: boolean` but has no stop callback.
- `ChatPanel`'s `send()` catch block (`ChatPanel.tsx:321-342`) already preserves
  partial streamed content on interruption, but tags it `ui.error:
  "Response interrupted: …"` (red) — wrong UX for a *user-initiated* stop.
- `ChatMessage.ui` (`types.ts:38-42`) is a closed object: `{ papers?, pending?,
  error? }`. A new `stopped?: boolean` field is needed.
- The streaming preview is the `buf`-backed `streaming` state + the pending
  `.msg-assistant.pending` row (`ChatPanel.tsx:374-378`). `buf` is reset to `""`
  at the start of every tool-loop round (`onAssistantStart`, `ChatPanel.tsx:266`),
  so it only ever holds the **current** round's partial text — exactly what we
  want to preserve on stop.

## Approach

**Chosen: A — `AbortController` in `ChatPanel`, signal wired only into the
streaming `fetch`.** Rejected alternatives:

- **B — also abort the in-flight `searchArxiv`/`webSearch` fetch mid-call.**
  More responsive during a long web search, but aborting a tool mid-execution
  leaves an assistant `tool_calls` message with no matching tool-result → the
  next turn's API history is invalid and needs repair logic. More code, more
  edges, low payoff (arXiv search is ~1s).
- **C — lift busy/abort into a zustand store shared across panels.**
  Over-engineering: `busy` is panel-local and only one panel is active at a
  time. No benefit.

**Why A is safe:** if the user clicks stop during a search, the current search
finishes (no signal on it), then the next `streamChat` call receives the already
aborted signal and rejects immediately. The conversation always ends with a
valid message sequence (any `tool_calls` already has its tool-result appended
before the next round), so the next turn's history stays legal. The streaming
`fetch` itself aborts instantly, which is the common case.

## Design

### 1. AbortController + `stop()` in `ChatPanel`

- Add `const abortRef = useRef<AbortController | null>(null);` to `ChatPanel`.
- In `send()`, right before `setBusy(true)`, create
  `const controller = new AbortController(); abortRef.current = controller;`
  and pass `signal: controller.signal` into the `runConversation({ … })` call.
- Add `function stop() { abortRef.current?.abort(); }`.
- In the `finally` block, clear `abortRef.current = null;` (existing
  `setBusy(false)` stays).

`lib/llm.ts` and `lib/api.ts` are **unchanged** — they already thread `signal`
through.

### 2. `ChatComposer`: arrow ↔ square toggle

`ChatComposer` gains one new prop:

```ts
onStop: () => void;
```

The send button (`ChatComposer.tsx:139-148`) becomes state-driven by `busy`:

| State | glyph | `onClick` | `disabled` | `title` | class |
|-------|-------|-----------|------------|---------|-------|
| idle (`!busy`) | `➤` | `onSend` | `!canSend` | "Send (Enter)" | `composer-send-btn` |
| busy | `■` | `onStop` | `false` | "Stop generating" | `composer-send-btn is-stop` |

Implementation: render the glyph from a ternary on `busy` (`➤` vs `■`); swap
`onClick`, `disabled`, `title`, and add the `is-stop` class when busy. The
button is **never disabled while busy** — that is the whole point.

`ChatPanel` passes `onStop={() => stop()}` to `<ChatComposer …/>`
(`ChatPanel.tsx:394-410`).

### 3. Catch block: distinguish user-abort from real error

In `send()`'s `catch (e: any)`, branch on whether *this* `send`'s controller was
aborted. The reliable local signal is `controller.signal.aborted` (the abort
originates here, so it is authoritative); also accept `e?.name === "AbortError"`
as a fallback:

```ts
} catch (e: any) {
  setStreaming("");
  setReasoning("");
  const aborted = controller.signal.aborted || e?.name === "AbortError";
  if (aborted) {
    // User-initiated stop. Keep partial content, mark "stopped" (not red).
    if (buf.trim()) {
      await appendMessages(c.id, [
        { role: "assistant", content: buf, ui: { stopped: true } },
      ]);
    }
    // If buf is empty (stopped during search/tool phase before any text),
    // append nothing — the turn just ends cleanly.
  } else {
    // Real error (network / upstream). Existing behavior preserved.
    const errMsg = e?.message || "error";
    if (buf.trim()) {
      await appendMessages(c.id, [
        { role: "assistant", content: buf, ui: { error: `Response interrupted: ${errMsg}` } },
      ]);
    } else {
      await appendMessages(c.id, [
        { role: "assistant", content: `⚠️ ${errMsg}`, ui: { error: String(errMsg) } },
      ]);
    }
  }
  setStatus("");
}
```

Note: `controller` is scoped inside `send()`, so the catch closes over the
correct controller even if a later `send()` created a new one (can't happen —
`if (busy) return` at the top blocks re-entry, but the closure makes it robust
regardless).

`maybeSummarizeTitle` is **not** called on abort (it sits after `runConversation`
in the `try`, which is skipped on throw). The instant truncated-first-message
title stays as the fallback. Correct: a stopped partial reply shouldn't drive
the title.

### 4. `MessageRow` renders the "stopped" marker

`MessageRow` (in `ChatPanel.tsx`, the assistant branch ~`ChatPanel.tsx:458-464`,
right after the existing `msg.ui?.error` line) adds, after the content:

```tsx
{msg.ui?.stopped && <div className="msg-stopped">已停止</div>}
```

This is a new, low-key marker — **not** `.msg-error` (red). Styled with
`--text-dim`.

### 5. `ChatMessage.ui` type

`types.ts:38-42` — add `stopped?: boolean;` to the `ui` object so the new
marker type-checks (typecheck is the project's only gate; no lint).

### 6. CSS (`index.css`)

- Add `.msg-stopped { color: var(--text-dim); font-size: 12px; margin-top: 6px; }`
  (mirrors `.msg-error`'s layout but dim, not red).
- Optional minimal tweak for the square state so the `■` reads as a clear stop
  target, e.g. `.composer-send-btn.is-stop { … }` — keep within the existing
  `.composer-icon-btn` look; do not restyle the whole button.

## Edge cases

- **Stop mid-final-answer:** `buf` has partial text → appended with
  `ui.stopped` → "已停止" shown. ✓
- **Stop during a search (no text yet):** search finishes, next `streamChat`
  rejects with `AbortError`, `buf` empty → nothing appended, turn ends cleanly,
  history valid (tool result already appended). ✓
- **Stop during tool-call round where model emitted text + tool_calls:** the
  assistant message (text + tool_calls) was already finalized/saved by
  `onAssistantMessage` before the tool ran; `buf` was reset on the next round's
  `onAssistantStart`. Nothing is lost; the interrupted round's empty `buf`
  appends nothing. ✓
- **Stop before any assistant content at all (e.g. first token not arrived):**
  `buf` empty → nothing appended. Button returns to arrow. ✓
- **Double-click stop / stop after generation ended:** `abortRef.current` is
  `null` (cleared in `finally`) → `stop()` is a no-op. ✓
- **Re-entry while busy:** `send()` guards with `if (busy) return;` — a second
  send can't start until `finally` runs. ✓
- **Mock-LLM E2E:** `tools/mock_llm.py` streams fast; to reproduce a mid-stream
  stop, add a small per-chunk delay in the mock's stream branch for the test
  (or have the driver click stop immediately after send). The mock's
  title-sniffing contract is untouched.

## Files changed

- `frontend/src/components/ChatPanel.tsx` — `abortRef`, `stop()`, `signal` into
  `runConversation`, catch branching, `onStop` passed to `ChatComposer`,
  `MessageRow` renders `ui.stopped`.
- `frontend/src/components/ChatComposer.tsx` — `onStop` prop, arrow↔square
  button toggle, `is-stop` class.
- `frontend/src/types.ts` — `stopped?: boolean` on `ChatMessage.ui`.
- `frontend/src/index.css` — `.msg-stopped` (+ optional `.is-stop` tweak).
- `frontend/src/lib/llm.ts`, `frontend/src/lib/api.ts` — **unchanged**
  (signal already threaded).

## Verification

1. `npm run typecheck` (the gate; no lint script).
2. E2E via the Playwright + mock-LLM rig:
   - Start backend `:8000`, frontend `:5173`, mock `:5050`.
   - `python tools/drive.py` (or a small new driver): send a message → while
     streaming, click the (now-square) stop button → assert (a) partial content
     is kept and shows "已停止", (b) button is back to arrow, (c) a new message
     can be sent afterward.
   - Also verify stop-during-search: trigger a `search_arxiv` turn and click
     stop mid-search → assert turn ends cleanly, next send works, no broken
     history.

## Out of scope

- Letting the user keep typing into the composer while busy (textarea stays
  `disabled={busy}`). Separate feature.
- Aborting the `searchArxiv`/`webSearch` fetch mid-call (Approach B).
- A stop affordance anywhere outside the chat composer (e.g. paper-view
  toolbar). The composer button covers both general chat and paper view since
  both render `ChatPanel` → `ChatComposer`.
- Persisting/restoring a "stopped" marker across reload — it is already
  persisted as part of the saved assistant message (`ui.stopped`), so it
  survives reload for free; no extra work.
