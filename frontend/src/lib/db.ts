// IndexedDB persistence for conversations + cached papers (full text).
// All data lives in the user's browser. No server storage.

import { openDB, type DBSchema, type IDBPDatabase } from "idb";
import type { Conversation, Paper } from "../types";

interface LaxDB extends DBSchema {
  conversations: {
    key: string;
    value: Conversation;
    indexes: { "by-updated": number };
  };
  papers: {
    key: string; // arxiv_id
    value: Paper & { full_text?: string; fetched_at: number };
  };
}

let dbp: Promise<IDBPDatabase<LaxDB>> | null = null;

function db(): Promise<IDBPDatabase<LaxDB>> {
  if (!dbp) {
    dbp = openDB<LaxDB>("little-alphaxiv", 1, {
      upgrade(d) {
        const c = d.createObjectStore("conversations", { keyPath: "id" });
        c.createIndex("by-updated", "updated_at");
        d.createObjectStore("papers", { keyPath: "arxiv_id" });
      },
    });
  }
  return dbp;
}

// ---- Conversations ----

export async function listConversations(): Promise<Conversation[]> {
  const d = await db();
  const all = await d.getAllFromIndex("conversations", "by-updated");
  return all.sort((a, b) => b.updated_at - a.updated_at);
}

export async function getConversation(id: string): Promise<Conversation | undefined> {
  const d = await db();
  return d.get("conversations", id);
}

export async function saveConversation(c: Conversation): Promise<void> {
  const d = await db();
  await d.put("conversations", c);
}

export async function deleteConversation(id: string): Promise<void> {
  const d = await db();
  await d.delete("conversations", id);
}

// ---- Papers (cache: metadata + extracted full text) ----

export async function getPaper(
  arxivId: string
): Promise<(Paper & { full_text?: string; fetched_at: number }) | undefined> {
  const d = await db();
  return d.get("papers", arxivId);
}

export async function savePaper(
  p: Paper & { full_text?: string; fetched_at: number }
): Promise<void> {
  const d = await db();
  await d.put("papers", p);
}
