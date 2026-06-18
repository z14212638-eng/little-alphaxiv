// Tool definitions + the client-side tool-calling loop.
//
// The backend is a dumb proxy; the LLM tool-calling loop runs HERE in the
// browser. The model gets two tools (search_arxiv, web_search). When it emits
// a tool_call, we execute it (calling /api/search or /api/websearch), append
// the result as a tool-role message, and re-stream — repeating until the
// model answers with plain text.

import { streamChat, searchArxiv, webSearch } from "./api";
import type { ChatMessage, Paper, Provider, ToolDef } from "../types";

export const SEARCH_TOOLS: ToolDef[] = [
  {
    type: "function",
    function: {
      name: "search_arxiv",
      description:
        "Search arXiv for academic papers by keyword, topic, or author. " +
        "Returns matching papers with title, authors, abstract, and a clickable link to preview the PDF. " +
        "Use this when the user wants to find, discover, or read research papers. " +
        "The query should be concise search terms (e.g. 'vision transformer', 'chain of thought reasoning').",
      parameters: {
        type: "object",
        properties: {
          query: {
            type: "string",
            description: "Search terms for arXiv. Concise keywords work best.",
          },
          max_results: {
            type: "number",
            description: "Max papers to return (default 8).",
          },
        },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "web_search",
      description:
        "General web search (via anysearch) for non-arXiv information: " +
        "recent news, blog posts, people, products, or anything not an academic paper. " +
        "Use search_arxiv for finding papers; use web_search for everything else.",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "Web search query." },
        },
        required: ["query"],
      },
    },
  },
];

interface LoopCallbacks {
  onAssistantStart?: () => void; // a new assistant message is beginning (clear stream buffer)
  onAssistantDelta?: (token: string) => void; // streaming token for current assistant message
  onAssistantMessage?: (msg: ChatMessage) => void; // an assistant message finalized
  onToolMessage?: (msg: ChatMessage) => void; // a tool-result message appended
  onPapers?: (papers: Paper[]) => void; // surfaced from search_arxiv results
  onStatus?: (status: string) => void;
  onReasoning?: (token: string) => void; // GLM reasoning_content tokens
}

/** Run a full conversation turn: stream the assistant response, executing any
 *  tool calls, until the model produces a final text answer. Returns the new
 *  messages to append (assistant messages + tool result messages). */
export async function runConversation(opts: {
  provider: Provider;
  messages: ChatMessage[];
  systemPrompt?: string;
  model?: string; // per-conversation model override
  signal?: AbortSignal;
  callbacks: LoopCallbacks;
}): Promise<{ newMessages: ChatMessage[] }> {
  const { provider, messages, systemPrompt, model: modelOverride, signal, callbacks } = opts;
  const effectiveModel = modelOverride || provider.model;

  // Build the message array sent to the model. Prepend system prompt.
  const apiMessages: unknown[] = [];
  if (systemPrompt)
    apiMessages.push({ role: "system", content: systemPrompt });
  for (const m of messages) {
    // strip UI-only fields before sending
    // For user messages with attachments, convert to OpenAI multimodal format
    if (m.role === "user" && m.attachments && m.attachments.length > 0) {
      const contentParts: unknown[] = [];
      if (m.content) contentParts.push({ type: "text", text: m.content });
      for (const att of m.attachments) {
        if (att.type === "image") {
          contentParts.push({
            type: "image_url",
            image_url: { url: att.data_url },
          });
        }
      }
      apiMessages.push({ role: "user", content: contentParts });
    } else {
      apiMessages.push({
        role: m.role,
        content: m.content,
        ...(m.tool_calls ? { tool_calls: m.tool_calls } : {}),
        ...(m.tool_call_id ? { tool_call_id: m.tool_call_id } : {}),
        ...(m.name ? { name: m.name } : {}),
      });
    }
  }

  const newMessages: ChatMessage[] = [];
  let guard = 0; // cap tool-call rounds to avoid infinite loops

  for (;;) {
    if (++guard > 6) break;
    callbacks.onAssistantStart?.();
    const result = await streamChat({
      provider,
      model: effectiveModel,
      messages: apiMessages,
      tools: SEARCH_TOOLS,
      signal,
      onDelta: (t) => callbacks.onAssistantDelta?.(t),
      onReasoning: (t) => callbacks.onReasoning?.(t),
    });

    const assistantMsg: ChatMessage = {
      role: "assistant",
      content: result.content || null,
      ...(result.tool_calls.length
        ? { tool_calls: result.tool_calls }
        : {}),
    };
    apiMessages.push(assistantMsg);
    newMessages.push(assistantMsg);
    callbacks.onAssistantMessage?.(assistantMsg);

    // No tool calls -> final answer, done.
    if (!result.tool_calls.length) break;

    // Execute each tool call and append results.
    for (const tc of result.tool_calls) {
      let args: any = {};
      try {
        args = JSON.parse(tc.function.arguments || "{}");
      } catch {
        /* keep empty args */
      }
      if (tc.function.name === "search_arxiv") {
        callbacks.onStatus?.("Searching arXiv…");
        const res = await searchArxiv(
          args.query ?? "",
          args.max_results ?? 8
        );
        callbacks.onPapers?.(res.results);
        const toolMsg: ChatMessage = {
          role: "tool",
          tool_call_id: tc.id,
          name: "search_arxiv",
          content: JSON.stringify(res.results.slice(0, 8)),
          ui: { papers: res.results },
        };
        apiMessages.push({
          role: "tool",
          tool_call_id: tc.id,
          name: "search_arxiv",
          content: JSON.stringify(res.results.slice(0, 8)),
        });
        newMessages.push(toolMsg);
        callbacks.onToolMessage?.(toolMsg);
      } else if (tc.function.name === "web_search") {
        callbacks.onStatus?.("Web searching…");
        const res = await webSearch(args.query ?? "", 8);
        const toolMsg: ChatMessage = {
          role: "tool",
          tool_call_id: tc.id,
          name: "web_search",
          content: JSON.stringify(res.results),
        };
        apiMessages.push({
          role: "tool",
          tool_call_id: tc.id,
          name: "web_search",
          content: JSON.stringify(res.results),
        });
        newMessages.push(toolMsg);
        callbacks.onToolMessage?.(toolMsg);
      } else {
        const toolMsg: ChatMessage = {
          role: "tool",
          tool_call_id: tc.id,
          name: tc.function.name,
          content: `unknown tool: ${tc.function.name}`,
        };
        apiMessages.push({
          role: "tool",
          tool_call_id: tc.id,
          name: tc.function.name,
          content: `unknown tool: ${tc.function.name}`,
        });
        newMessages.push(toolMsg);
        callbacks.onToolMessage?.(toolMsg);
      }
    }
    // loop again: model will see tool results and either call more tools or answer
  }

  return { newMessages };
}
