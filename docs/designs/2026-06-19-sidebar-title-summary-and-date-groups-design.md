# Sidebar: LLM-summarized conversation titles + date groups

**Date:** 2026-06-19
**Branch:** `worktree-conv-title-and-dates` (worktree off `origin/main`)

## Problem

The left sidebar indexed each conversation by the user's first message
truncated to 48 chars (`ChatPanel.send()` → `rename(c.id, text.slice(0, 48))`).
That is too blunt — a long question becomes a cut-off fragment, and paper
threads are indistinguishable. There was also no time organization: every
entry sat in one flat MRU list.

## Design

### 1. LLM-summarized titles

- New `completeChat()` in `lib/api.ts` — a **non-streaming** chat completion
  (`stream:false`). The `/api/llm` proxy already forwards non-streaming
  requests as plain JSON (`backend/app/routers/llm.py`). Used for short
  one-shot calls where SSE + tool-loop overhead is wasted.
- New `generateConversationTitle()` in `lib/llm.ts` — asks the conversation's
  effective model (per-conversation override → provider default, same
  precedence as `streamChat`) to summarize the first exchange (user question +
  assistant reply) into a ≤10-word title. For paper chats it is grounded in
  the paper's arxiv id / title / abstract / full-text excerpt. Output is
  normalized by `cleanTitle()` (strip quotes/labels, collapse whitespace, drop
  trailing period, cap ~80 chars). Returns `null` on any failure.
- `ChatPanel.send()` keeps the instant truncated-first-message title for
  immediate sidebar feedback, then — after the first turn's assistant reply
  lands — fires `maybeSummarizeTitle()` (fire-and-forget, never throws) which
  calls `generateConversationTitle()` and `rename()`s to the result. Triggered
  exactly once (guarded by `wasFirstTurn = c.messages.length === 0` at send
  start). Graceful fallback: on failure / no provider, the truncated title
  stays. No backfill of existing conversations (their first-message titles
  remain valid).
- Paper chats always pass at least the arxiv id in the title context (even
  before metadata/full text is cached), so the model always knows it is a
  paper thread.

### 2. Date-grouped sidebar (alphaxiv-style)

- New pure helper `lib/dates.ts` `groupByDate()` buckets items into
  **Today / Yesterday / Previous 7 Days / Previous 30 Days / `<Month Year>`**
  (reverse-chron; items keep input order so each section stays MRU). `now` is
  injectable for deterministic tests. Vitest covers the buckets, month
  ordering, and within-bucket order preservation.
- `Sidebar.tsx` renders conversation groups under `.conv-group` /
  `.conv-group-label` headers (CSS already existed from the themes commit,
  unused until now). General chats + paper groups are bucketed by
  `updated_at`.

### 3. Paper-group label: the paper's real title, not the bare id

The sidebar groups all threads for one `paper_id` into a single row (see
`Sidebar.tsx` → `paperGroups`). The row's label is **not** the bare paper id —
it resolves to the paper's real title so an opaque id like `sha256:<hash>` (a
locally-uploaded PDF with no DOI; see backend `paper_uploads.py`) never reaches
the UI.

Resolution order (`paperGroupLabel(paperId, cachedTitle, rep)` in `Sidebar.tsx`):

1. The paper's cached title, looked up via `db.getPaper(paperId)` inside a
   per-paper-id-set `useEffect`. This heals rows created before this fix —
   previously titled `📄 sha256:…` — to the real title on the next sidebar
   load, without waiting for the user to ask a question.
2. The most-recent thread's own title (the LLM summary from §1), when it's a
   real title.
3. `📄 <paperId>` fallback — only when no title is known yet (e.g. a bare-id
   arXiv stub opened while arXiv is unreachable).

New paper threads are titled the same way at creation: `PaperView` calls
`paperThreadTitle(p, arxivId)` (`lib/paperMeta.ts`, reuses `hasRealTitle`) at
both creation sites (the init effect + `handleNewConversation`). The first user
message then retitles the thread to the LLM summary via `maybeSummarizeTitle`
(unchanged). So a freshly-opened uploaded PDF shows its real title in the
sidebar immediately — not `📄 sha256:179…` until the user asks a question.

### 4. Process

Implemented in an isolated worktree off `origin/main` (the main worktree
held another agent's uncommitted model-list/codebox work, which this branch
does not touch). Verified with the mock-LLM Playwright rig:
`tools/drive_titles.py` (new) confirms both general and paper chats get
summarized titles + a `Today` group, with no page errors; `tools/drive_fixes.py`
(extended to honor `APP_URL`) confirms no regression in empty-conv spam,
paper grouping, or the history panel. `mock_llm.py` was extended to answer
title-generation requests with distinct general/paper canned titles.

## Files

- `frontend/src/lib/api.ts` — `completeChat()`
- `frontend/src/lib/llm.ts` — `generateConversationTitle()`, `cleanTitle()`
- `frontend/src/lib/dates.ts` (+ `dates.test.ts`) — `groupByDate()`
- `frontend/src/components/ChatPanel.tsx` — `maybeSummarizeTitle()` + first-turn wiring
- `frontend/src/components/Sidebar.tsx` — date-grouped render + `paperGroupLabel()` + paper-cache title `useEffect`
- `frontend/src/lib/paperMeta.ts` — `paperThreadTitle()` (titles new paper threads from the cached real title)
- `tools/mock_llm.py` — title-request handling
- `tools/drive_titles.py` (new), `tools/drive_fixes.py` — verification

## Out of scope

- No backfill of existing conversation titles.
- No date grouping in the paper-view `HistoryPanel` (left sidebar only, per
  request).
- `drive_fixes.py` Step 5 (theme) has a pre-existing stale selector unrelated
  to this change; left untouched.
