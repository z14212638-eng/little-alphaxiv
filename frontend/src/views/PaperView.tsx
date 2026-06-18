// Paper view (Flow B): two-panel layout with a draggable divider.
// left -> PdfViewer, right -> ChatToolbar + (ChatPanel | HistoryPanel)
//
// Conversation model:
//   - A paper can have many threads (sub-conversations). They all group into
//     ONE left-sidebar entry per paper (see Sidebar).
//   - Threads are managed here via the HistoryPanel (toggle from the toolbar),
//     not as separate sidebar rows.
//   - Empty threads live in memory only; the first message persists them and
//     re-titles them to the user's question. "New conversation" reuses an
//     existing empty thread instead of stacking empties.

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { PdfViewer } from "../components/PdfViewer";
import { ChatPanel } from "../components/ChatPanel";
import { ChatToolbar } from "../components/ChatToolbar";
import { HistoryPanel } from "../components/HistoryPanel";
import { useConversations } from "../store/conversations";
import { useSettings } from "../store/settings";
import { useUi } from "../store/ui";
import { useAnnotations } from "../store/annotations";
import * as db from "../lib/db";
import type { StylePreset } from "../types";

export function PaperView() {
  const { arxivId, convId } = useParams<{ arxivId: string; convId?: string }>();
  const navigate = useNavigate();
  const conversations = useConversations((s) => s.conversations);
  const loaded = useConversations((s) => s.loaded);
  const create = useConversations((s) => s.create);
  const setActive = useConversations((s) => s.setActive);
  const updateSettings = useConversations((s) => s.updateSettings);
  const defaultProviderId = useSettings((s) => s.defaultProviderId);
  const collapseSidebar = useUi((s) => s.collapseSidebar);
  const [convIdState, setConvId] = useState<string | null>(convId ?? null);
  const [fullText, setFullText] = useState<string | null>(null);
  const [extracting, setExtracting] = useState(true);
  const [showHistory, setShowHistory] = useState(false);
  const initRef = useRef<string | null>(null);

  useEffect(() => { collapseSidebar(); }, [collapseSidebar]);

  useEffect(() => {
    if (!arxivId) return;
    db.getPaper(arxivId).then((p) => {
      if (p?.full_text) { setFullText(p.full_text); setExtracting(false); }
    });
  }, [arxivId]);

  const loadAnnots = useAnnotations((s) => s.load);
  useEffect(() => {
    if (!arxivId) return;
    loadAnnots(arxivId);
  }, [arxivId, loadAnnots]);

  // Resolve which thread to show once data is loaded (or on paper change).
  // Uses a ref guard so message appends / thread switches don't re-trigger.
  useEffect(() => {
    if (!arxivId || !loaded) return;
    if (initRef.current === arxivId) return;
    initRef.current = arxivId;

    // Validate the URL's convId (it may be stale if it was an un-persisted
    // empty thread from a previous session).
    if (convId) {
      const c = conversations.find(
        (x) => x.id === convId && x.type === "paper" && x.paper_id === arxivId
      );
      if (c) { setActive(convId); setConvId(convId); return; }
    }
    // Pick the most-recently-touched thread for this paper.
    const threads = conversations
      .filter((c) => c.type === "paper" && c.paper_id === arxivId)
      .sort((a, b) => b.updated_at - a.updated_at);
    if (threads[0]) {
      setActive(threads[0].id);
      setConvId(threads[0].id);
      navigate(`/paper/${arxivId}/${threads[0].id}`, { replace: true });
      return;
    }
    // No thread yet — create an empty one (in-memory until first message).
    create({
      type: "paper",
      paperId: arxivId,
      title: `📄 ${arxivId}`,
      reuseEmpty: true,
      providerId: defaultProviderId ?? undefined,
    }).then((c) => {
      setActive(c.id);
      setConvId(c.id);
      navigate(`/paper/${arxivId}/${c.id}`, { replace: true });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [arxivId, loaded]);

  // Keep local state in sync when the URL convId changes (history panel nav).
  useEffect(() => {
    if (convId && convId !== convIdState) setConvId(convId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId]);

  const onTextExtracted = useCallback((text: string) => {
    setFullText(text); setExtracting(false);
    if (arxivId) {
      db.getPaper(arxivId).then((cached) => {
        db.savePaper({ arxiv_id: arxivId, title: cached?.title ?? arxivId, authors: cached?.authors ?? [], abstract: cached?.abstract ?? "", pdf_url: cached?.pdf_url ?? "", abs_url: cached?.abs_url ?? "", published: cached?.published ?? "", primary_category: cached?.primary_category ?? "", full_text: text, fetched_at: Date.now() });
      });
    }
  }, [arxivId]);

  const systemPrompt = fullText
    ? PAPER_SYSTEM_PREAMBLE(arxivId!) + `\n\n=== PAPER FULL TEXT ===\n${fullText}\n=== END ===`
    : PAPER_SYSTEM_PREAMBLE(arxivId!) + `\n\n[Note: full paper text is still loading; answer based on what you can.]`;

  function handleNewConversation() {
    if (!arxivId) return;
    create({
      type: "paper",
      paperId: arxivId,
      title: `📄 ${arxivId}`,
      reuseEmpty: true, // reuse an existing empty thread if present
      providerId: defaultProviderId ?? undefined,
    }).then((c) => {
      setActive(c.id);
      setConvId(c.id);
      navigate(`/paper/${arxivId}/${c.id}`, { replace: true });
      setShowHistory(false);
    });
  }

  function handleSelectConversation(id: string) {
    setActive(id);
    setConvId(id);
    if (arxivId) navigate(`/paper/${arxivId}/${id}`, { replace: true });
    setShowHistory(false);
  }

  if (!arxivId) return null;

  return (
    <main className="main-pane paper-view">
      <ResizablePanels
        left={<PdfViewer arxivId={arxivId} onTextExtracted={onTextExtracted} />}
        right={
          <div className="chat-col-inner">
            {convIdState && (
              <ChatToolbar
                conversationId={convIdState}
                arxivId={arxivId}
                showHistory={showHistory}
                onToggleHistory={() => setShowHistory((v) => !v)}
                onNewConversation={handleNewConversation}
                onModelChange={(m) => updateSettings(convIdState, { model: m })}
                onStyleChange={(s: StylePreset) => updateSettings(convIdState, { style_preset: s })}
                onContextWindowChange={(ctx) => updateSettings(convIdState, { context_window: ctx })}
              />
            )}
            {showHistory ? (
              <HistoryPanel
                arxivId={arxivId}
                activeId={convIdState}
                onSelect={handleSelectConversation}
                onNew={handleNewConversation}
                onClose={() => setShowHistory(false)}
              />
            ) : (
              <>
                {extracting && <div className="paper-status">Reading paper for context...</div>}
                {convIdState ? (
                  <ChatPanel conversationId={convIdState} systemPrompt={systemPrompt} showPaperLinks={false} />
                ) : (
                  <div className="chat-shell"><div className="chat-empty">Starting discussion...</div></div>
                )}
              </>
            )}
          </div>
        }
      />
    </main>
  );
}

function ResizablePanels({ left, right }: { left: React.ReactNode; right: React.ReactNode }) {
  const [ratio, setRatio] = useState<number>(() => {
    const saved = Number(localStorage.getItem("lax-paper-ratio"));
    return saved > 0.1 && saved < 0.9 ? saved : 0.58;
  });
  const dragging = useRef(false);
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!dragging.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const r = (e.clientX - rect.left) / rect.width;
      setRatio(Math.max(0.2, Math.min(0.8, r)));
    }
    function onUp() {
      if (dragging.current) {
        dragging.current = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        localStorage.setItem("lax-paper-ratio", String(ratio));
      }
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, [ratio]);
  return (
    <div className="resizable-panels" ref={containerRef}>
      <div className="pdf-col" style={{ flexBasis: `${ratio * 100}%` }}>{left}</div>
      <div className="panel-divider" onMouseDown={() => { dragging.current = true; document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none"; }}>
        <div className="panel-divider-grip" />
      </div>
      <div className="chat-col" style={{ flexBasis: `${(1 - ratio) * 100}%` }}>{right}</div>
    </div>
  );
}

function PAPER_SYSTEM_PREAMBLE(arxivId: string): string {
  return `You are a research assistant helping the user read and understand the arXiv paper ${arxivId}.
The user can see the PDF on the left and is chatting with you on the right.
The full text of the paper is provided below. Answer questions about its content - methods, datasets, results, limitations, related work - grounded in the provided text.
If the user asks something not covered in the paper, say so. Be concise and precise. Format answers with markdown.
You have a search_arxiv tool if the user wants to find related papers.`;
}
