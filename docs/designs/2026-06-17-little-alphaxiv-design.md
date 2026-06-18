# Little Alphaxiv — 设计文档

**日期**: 2026-06-17
**状态**: 已定稿，进入实现

## 1. 目标

复刻一个 alphaxiv 体验给自己和几个朋友用，解决当前痛点：
- VSCode 本地索引 PDF 麻烦，每次要叫 AI 下载论文再打开，右边开 Claude Code 插件对话，割裂。
- 想要 alphaxiv 那种：先和 AI 对话找论文 → AI 给链接 → 点击立刻 PDF 预览（左栏）+ Assistant 对话（右栏）→ 会话管理。
- 现有 alphaxiv 不能用自己的模型、有配额限制。

v1 只做两个核心功能：**Paper 预览** + **Assistant 对话**。

## 2. 已定决策（brainstorming 结论）

| 维度 | 决策 |
|---|---|
| 形态 | 自托管 Web app |
| 栈 | Python (FastAPI) + React (Vite + TS) |
| 用户模型 | **无 server 端 user**。每人各自跑，所有数据 + API key 都在各自浏览器（localStorage + IndexedDB）。无 auth、无 DB。 |
| Backend 角色 | 无状态 CORS 代理（~200 行），零存储，不认识谁是谁。只为绕过浏览器 CORS。 |
| 搜索源 | arXiv API（原生，免费无 key）+ anysearch MCP（web 兜底） |
| 论文正文感知 | 全文塞 context，依赖 provider prompt caching；客户端 pdf.js `getTextContent()` 抽取 |
| PDF 渲染 | pdf.js 浏览器端渲染 |
| LLM 协议 | **只 OpenAI 兼容**（`/chat/completions` + tools），两条 baseURL 都走同协议 |
| 流式 | SSE |
| 借鉴 book-to-skill | "extract once, cache, query on-demand" → 论文全文抽一次按 arxiv_id 缓存 IndexedDB；v1 全文入 context（靠 caching），切片留 v2 |

## 3. 架构

```
React frontend (Vite + TS)
 ├─ localStorage:  provider 设置 [{name, base_url, api_key, model, default?}]
 ├─ IndexedDB:
 │    conversations: {id, title, type: general|paper, paper_id?, messages[], created_at}
 │    papers:        {arxiv_id, title, authors, abstract, pdf_url, full_text, fetched_at}
 ├─ pdf.js viewer (左) + chat panel (右)
 └─ pdf.js getTextContent() 客户端抽正文
        │  每次请求带 key（header）
        ▼
FastAPI 无状态代理 (~200 行, 零存储)
 ├─ POST /api/llm      → 透传 OpenAI 兼容 /chat/completions + SSE 回流
 ├─ GET  /api/search   → 透传 arXiv API (arxiv.org 无 CORS)
 ├─ GET  /api/websearch→ anysearch MCP client (非 arxiv 兜底)
 └─ GET  /api/pdf      → 抓 arxiv PDF + 透传 + 磁盘缓存 (arxiv PDF 无 CORS)
```

**CORS 是 backend 存在的唯一硬约束**，与 user 模型无关：
- OpenAI 兼容网关多数不发 CORS 头
- arxiv.org 搜索 API 不发 `Access-Control-Allow-Origin`
- arxiv.org PDF 文件不带 CORS 头，pdf.js 渲染不了

## 4. 核心流程

### Flow A — 发现对话
前端发 `/api/llm`（带用户 key + `tools=[search_arxiv, web_search]`）→ 模型返回 `tool_call(search_arxiv)` → **前端执行**工具循环（调 `/api/search`）→ 结果回填模型 → 模型给自然语言回答 → 前端把 `search_arxiv` 工具结果渲染成**可点击论文卡片** → 点卡片跳 `/paper/<arxiv_id>`。

工具循环放前端，backend 保持无脑透传。

### Flow B — 论文预览 + 对话
进 `/paper/<id>` → 前端查 IndexedDB 缓存 → `/api/pdf/<id>` 取 PDF → pdf.js 左栏渲染 → 渲染时 `getTextContent()` 抽全文存 IndexedDB → 右栏新会话，全文注入 context（"You are discussing this paper: <FULL TEXT>"）→ 对话每轮靠 provider prompt caching，重发全文只算 cache-read 价。

## 5. anysearch MCP 接入
backend 跑 MCP client 连用户现有 anysearch server，`/api/websearch` 透传，作为 `web_search` 工具的非 arxiv 兜底。需用户提供 anysearch MCP server 启动命令/transport 配置。

## 6. 项目结构
```
little_alphaxiv/
  backend/
    main.py          # FastAPI app, ~200 行透传路由
    llm_proxy.py     # /api/llm 透传 + SSE
    arxiv.py         # /api/search arXiv 透传
    mcp_client.py    # anysearch MCP client
    pdf_cache.py     # /api/pdf 代理 + 磁盘缓存
  frontend/
    src/
      views/         # GeneralChat, PaperView, Settings
      components/    # ChatPanel, PdfViewer, PaperCard, Sidebar
      lib/           # api.ts, llm.ts (工具循环), extract.ts (getTextContent)
      store/         # zustand: settings, conversations
  docs/designs/2026-06-17-little-alphaxiv-design.md
```

## 7. 构建顺序（增量，每步可验证）
1. backend `/api/llm` 透传 + SSE，纯聊天跑通
2. `/api/search`（arXiv）+ 前端工具循环 → "找 X 论文"出可点卡片
3. `/api/pdf` + pdf.js 渲染 → PDF 能看
4. `getTextContent` + 论文 context 注入 → 论文问答跑通
5. IndexedDB 持久化（会话+论文）→ 刷新不丢
6. anysearch `/api/websearch` + `web_search` 工具
7. 设置页 + 多 provider 切换
8. 打磨：会话侧边栏、流式 UX、错误处理

## 8. 安全说明
localStorage 存 key 有 XSS 风险，但自托管 + 几个信任的人 + 用户控制代码，威胁模型可接受，同类工具都这么做。

## 9. 非 v1 范围（YAGNI）
- 切片/RAG 向量检索（留 v2，论文爆 context 时）
- 跨设备同步
- 多论文同时对话
- Semantic Scholar 引用数据
- auth / user 系统
