// App shell: sidebar + routed main pane. Loads conversations on mount.

import { useEffect } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { ChatView } from "./views/ChatView";
import { PaperView } from "./views/PaperView";
import { SettingsView } from "./views/SettingsView";
import { useConversations } from "./store/conversations";
import { useSettings } from "./store/settings";

export default function App() {
  const load = useConversations((s) => s.load);
  const create = useConversations((s) => s.create);
  const setActive = useConversations((s) => s.setActive);
  const conversations = useConversations((s) => s.conversations);
  const loaded = useConversations((s) => s.loaded);
  const navigate = useNavigate();
  const defaultProviderId = useSettings((s) => s.defaultProviderId);

  useEffect(() => {
    load();
  }, [load]);

  // On the root path: open the most recent conversation, or create a fresh
  // general chat. Empty chats are never persisted, so after a reload there are
  // no leftover empty "New chat" rows.
  async function ensureRootChat() {
    if (conversations.length > 0) {
      const first = conversations[0];
      setActive(first.id);
      navigate(first.type === "paper" ? `/paper/${first.paper_id}/${first.id}` : `/chat/${first.id}`);
      return;
    }
    const c = await create({ type: "general", providerId: defaultProviderId ?? undefined });
    setActive(c.id);
    navigate(`/chat/${c.id}`);
  }

  return (
    <div className="app">
      <Sidebar />
      <Routes>
        <Route path="/" element={<RootLanding loaded={loaded} onMount={ensureRootChat} />} />
        <Route path="/chat/:id" element={<ChatView />} />
        <Route path="/paper/:arxivId" element={<PaperView />} />
        <Route path="/paper/:arxivId/:convId" element={<PaperView />} />
        <Route path="/settings" element={<SettingsView />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}

function RootLanding({ loaded, onMount }: { loaded: boolean; onMount: () => void }) {
  useEffect(() => {
    if (!loaded) return;
    onMount();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded]);
  return <main className="main-pane"><div className="chat-empty">Starting…</div></main>;
}
