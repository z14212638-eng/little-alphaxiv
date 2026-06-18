# Little Alphaxiv

A self-hosted, alphaxiv-style paper-reading app for you and a few friends.
Chat with an AI to find arXiv papers, click a link, and the PDF opens in the
left panel with a paper-aware assistant ready in the right panel. Bring your
own OpenAI-compatible API key — no quotas, no shared accounts, all data stays
in your own browser.

```
 ┌─ Sidebar ─┐  ┌── General chat ──┐     ┌── Paper view ──────────────────┐
 │ + New chat│  │  find me papers   │     │  PDF (pdf.js)  │  Assistant     │
 │ conv list │  │  on X ...         │     │  preview       │  (paper text   │
 │ ⚙ Settings│  │  [clickable cards]│ ──▶ │  (left)        │   in context)  │
 └───────────┘  └───────────────────┘     └────────────────┴────────────────┘
```

## What's here

- **`backend/`** — a stateless FastAPI CORS proxy (~5 small files). Stores
  nothing. Exists only because arXiv and most LLM gateways send no CORS
  headers, so the browser can't reach them directly. Your API key is sent
  per-request from the browser and never persisted server-side.
- **`frontend/`** — Vite + React + TypeScript SPA. All state (providers,
  conversations, cached paper text) lives in your browser (localStorage +
  IndexedDB).
- **`docs/designs/`** — the validated design doc.

## How it works

- **Discovery (general chat):** you describe what you want; the assistant
  calls a `search_arxiv` tool (and optionally `web_search`); results render as
  clickable paper cards. Click → paper view.
- **Paper view:** the PDF loads via the proxy (cached to disk); pdf.js
  extracts the full text once (`getTextContent`), cached in IndexedDB; that
  text is injected into the chat context so the assistant can discuss the
  paper's actual content. Re-sending the full text each turn is cheap via
  provider prompt caching.
- **Tools run in the browser.** The backend is a dumb pipe; the OpenAI-style
  tool-calling loop lives in the frontend (`src/lib/llm.ts`).

## Run it

### 1. Backend (proxy)

```bash
cd backend
# uses your Agent_env conda env if present (see run.sh)
./run.sh
# or manually:
#   pip install -r requirements.txt
#   uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 2. Frontend (app)

```bash
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173 . Vite proxies `/api/*` → `http://127.0.0.1:8000`.

### 3. Configure a provider

Click ⚙ Settings → add an OpenAI-compatible provider:

| Field    | Example                                   |
|----------|-------------------------------------------|
| Name     | OpenAI / My Gateway                       |
| Base URL | `https://api.openai.com/v1`               |
| API key  | `sk-...`                                  |
| Model    | `gpt-4o-mini`                             |

Any OpenAI-compatible endpoint works (OpenAI, an OpenAI-compatible Anthropic
gateway, local Ollama with the OpenAI shim, one-api/new-api, etc.). Set one
as default. Keys are stored only in this browser's localStorage.

Then start a new chat and ask: *"find me recent papers on vision transformers"*.

## Status

| Step | Feature                              | State |
|------|--------------------------------------|-------|
| 1    | LLM proxy + pure chat (streaming)    | ✅ verified |
| 2    | arXiv search + tool-calling loop     | ✅ backend verified; browser E2E pending your key |
| 3    | PDF proxy + pdf.js preview           | ✅ verified (fetch+cache+range) |
| 4    | Full-text extraction + paper chat    | ✅ coded; E2E pending |
| 5    | IndexedDB persistence                | ✅ coded |
| 6    | anysearch MCP (`web_search`)         | ⏳ stub — real wiring TODO |
| 7    | Settings + multi-provider            | ✅ coded |
| 8    | Polish (sidebar, streaming UX)       | 🔧 partial |

### Known TODO

- **Step 6 — real anysearch MCP wiring.** `backend/app/routers/websearch.py`
  is a placeholder. Wiring your existing anysearch MCP server means running an
  MCP client (stdio or HTTP transport) in the backend and proxying `search`
  calls. I need your anysearch server's launch command / transport to finish
  this.
- **Direct paper-URL navigation** (pasting `/paper/<id>`) works but has no
  paper title/abstract metadata until extraction runs; the model reads the
  title from the extracted text. Fetching metadata by arxiv id via `id_list`
  is a small backend addition if you want it.
- **E2E browser test** of the full search → card → PDF → extract → chat loop
  needs your real API key in the UI. Backend pieces are individually verified.

## Project layout

```
little_alphaxiv/
  backend/
    app/main.py              # FastAPI app, CORS, routers
    app/routers/llm.py       # /api/llm — OpenAI-compatible passthrough + SSE
    app/routers/search.py    # /api/search — arXiv Atom API → JSON
    app/routers/pdf.py       # /api/pdf/{id} — arXiv PDF proxy + disk cache + ranges
    app/routers/websearch.py # /api/websearch — anysearch MCP (STUB)
    run.sh, requirements.txt
  frontend/
    src/lib/api.ts           # fetch wrappers + SSE stream parser
    src/lib/llm.ts           # client-side tool-calling loop
    src/lib/extract.ts       # pdf.js getTextContent full-text extraction
    src/lib/db.ts            # IndexedDB (conversations + papers)
    src/store/{settings,conversations}.ts  # zustand stores
    src/components/{ChatPanel,PdfViewer,PaperCard,Sidebar}.tsx
    src/views/{ChatView,PaperView,SettingsView}.tsx
  docs/designs/2026-06-17-little-alphaxiv-design.md
```

## Security note

API keys live in browser localStorage (XSS-exposed). This is acceptable for a
self-hosted tool shared among a few trusted people where you control the code.
Don't expose the URL publicly without adding auth.
