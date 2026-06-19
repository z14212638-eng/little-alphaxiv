# 多搜索源（OpenAlex + Semantic Scholar）设计

**日期**: 2026-06-19
**状态**: 设计稿，待评审

## 背景与目标

当前 App 只有一个默认的 arXiv 搜索源（`search_arxiv` 工具 → `/api/search`）。本次新增
**OpenAlex** 与 **Semantic Scholar** 两个可选学术搜索源，配置放在主界面设置
（现有设置只有主题与 Provider），让用户自己填 key 并附外链跳转防止用户不知道去哪拿 key。
未填 key 时两个源不启用，**默认回滚到 arXiv**。

两个关键决策（已与用户确认）：

1. **多源共存模式 = 模型按需选源**：每个源暴露为独立 LLM 工具，仅在已启用时出现；
   arXiv 始终可用作默认兜底。系统提示引导模型按场景选源。
2. **非 arXiv 结果 = 尽力 in-app（OA PDF）**：有 arXiv-id 走现有 in-app 预览；
   否则有开放获取（OA）PDF 就在现有 PaperView 里通过扩展代理拉取预览；
   都没有则外链跳转落地页。

实现方案选 **A：保留 `arxiv_id` 作不透明键（不动 IDB schema）+ 扩展 PDF 代理能拉任意
OA URL + OA 结果走现有完整 PaperView**。

## 关键事实：arxiv_id 耦合其实很浅

经全仓追踪确认，以下部分**已经把 id 当不透明字符串处理**，非 arXiv id 直接可用、无需改动：

- 路由参数 `/paper/:arxivId`（`App.tsx:46-47`）及所有 `/paper/${id}` 导航 —— 纯字符串，无校验。
- `Conversation.paper_id`（`types.ts:80`）、conversations store（`store/conversations.ts`）、
  侧栏分组（`Sidebar.tsx:62-75`）、HistoryPanel 过滤（`HistoryPanel.tsx:41`）、
  ChatToolbar 过滤（`ChatToolbar.tsx:48`）—— 全是字符串相等分组。
- IDB `papers` / `annotations` store（`lib/db.ts:31-44`）—— `arxiv_id` 是字段名，
  但 IDB 把它当不透明字符串键；非 arXiv id 今天就能用。（重命名需 v3 迁移，推迟。）
- `PdfViewer`（`components/PdfViewer.tsx`）—— 通用 pdf.js 渲染器；唯一耦合是
  `pdfUrl(arxivId)` 调用。
- 文本抽取（`lib/extract.ts` 及 PdfViewer 内联副本）—— 通用 `getTextContent`。
- `extractArxivId`（`lib/arxiv.ts`）—— 仅被 `Markdown.tsx:32` 用于 arXiv 链接→in-app 卡重写；
  非 arXiv 链接已走外链分支，**不破坏**。

真正 arXiv 硬编码、需要改动的只有：

1. `backend/app/routers/pdf.py:37-52` 的 `_fetch_from_arxiv`（硬编码 `arxiv.org/pdf/<base>.pdf`
   + `split("v")[0]` 版本剥离）。
2. `frontend/src/lib/api.ts:219-221` 的 `pdfUrl(arxivId)`（假设 `/api/pdf/<arxiv id>`）。
3. 两条文案（`PaperView.tsx:258` 的 `PAPER_SYSTEM_PREAMBLE` "arXiv paper…"、
   `llm.ts:251` title-gen 的 `arxiv id:`）—— 装饰性。
4. `search.py` 是**唯一**的 `Paper` 元数据来源；非 arXiv PDF 需新元数据来源
   （本次由搜索结果种入，见 §6）。

## 数据模型

### `Paper`（`frontend/src/types.ts`）—— 加可选字段，arXiv 结果不受影响

```ts
export interface Paper {
  arxiv_id: string;            // 不透明键；非 arXiv 时为稳定 id（见下）
  title: string;
  authors: string[];
  abstract: string;
  pdf_url: string;
  abs_url: string;
  published: string;
  primary_category: string;
  // 新增（可选）：
  source?: "arxiv" | "openalex" | "s2";
  doi?: string;
  oa_pdf_url?: string;         // 非 arXiv 且有 OA 时的直链 PDF
  external_url?: string;       // 无 OA 时的外链落地页（DOI/S2/OpenAlex）
}
```

### 稳定 id 方案（决定能否 in-app 打开）

`arxiv_id` 字段始终作为 IDB 键 + 路由参数 + React key 使用。取值按优先级：

1. 有 arXiv-id（S2 `externalIds.ArXiv` / OpenAlex DOI 前缀 `10.48550/arxiv.`）→ 用裸 arXiv id，
   走现有 `/api/pdf/<id>`。
2. 否则有 DOI → `doi:<doi>`（路由里 `encodeURIComponent`）。
3. 否则有 OA PDF → `<source>:<sourceid>`（`s2:<paperId>` / `openalex:<id>`），仍作路由参数
   进 PaperView（经 `oa_pdf_url` 走 `/api/pdf-url`）。
4. 既无 arXiv-id 又无 OA PDF → 不进 in-app（不留稳定 id），卡 click 直跳 `external_url`。

### Settings store（`frontend/src/store/settings.ts`）—— 加 `searchSources` 切片

随 zustand `persist` 自动落 `little-alphaxiv-settings`：

```ts
interface SearchSources {
  openalex: { enabled: boolean; apiKey: string; email: string };
  semanticScholar: { enabled: boolean; apiKey: string };
}
```

默认两个都 `enabled:false`（老用户无感 → 仅 arXiv）。启用**不强制填 key**（两源无 key 也可用，
只是限速），key 为可选增强；UI 旁附「获取 key」外链。

## 后端

### 新增 `backend/app/routers/openalex.py`

`GET /api/openalex?q=&max_results=&api_key=&email=`。

- 请求 `https://api.openalex.org/works?search=&per_page=&mailto=&api_key=`。
- 字段映射：`title`、`authorships[].author.display_name`、`publication_date`、
  `primary_topic`、`open_access` / `best_oa_location.pdf_url`、`doi`、`id`。
- 摘要从 `abstract_inverted_index` 重建（抽成纯函数）。
- arXiv 检测靠 DOI 前缀 `10.48550/arxiv.`。
- 返回 `{ total, results: Paper[] }`，归一到上面 Paper 形状（带 `source`/`doi`/`oa_pdf_url`/`external_url`）。
- 在 `main.py` 注册。

### 新增 `backend/app/routers/semantic_scholar.py`

`GET /api/semantic_scholar?q=&max_results=&api_key=`。

- 请求 `https://api.semanticscholar.org/graph/v1/paper/search?query=&limit=&fields=title,abstract,authors,year,externalIds,openAccessPdf,url`。
- `externalIds.ArXiv` 优先作 arXiv-id；`openAccessPdf.url` 作 `oa_pdf_url`；`year` 作 `published`。
- 返回 `{ total, results: Paper[] }`，归一到 Paper 形状。
- 在 `main.py` 注册。

arXiv 的 `search.py` **不动**（前端给结果打 `source:"arxiv"` 标签即可）。

### 扩展 PDF 代理（`backend/app/routers/pdf.py`）

新增 `GET /api/pdf-url?url=<encoded>`，与现有 `/api/pdf/{arxiv_id}` 并存、互不干扰：

- 校验 scheme ∈ {http, https}；解析 host，解析 DNS 后拒绝私网/回环/链路本地 IP（SSRF 加固）。
- 缓存键 = `sha256(url)` 十六进制（现有 `_cache_path` 的 `safe` 方案吃不下 URL 字符且会碰撞）。
- 复用现有 range/206/416 逻辑（`_serve_bytes`）；30s 超时；跟随重定向。
- 502/400 错误与现有风格一致。

**SSRF 说明**：这是一个受控开放代理，但 URL 来自我们自己的搜索结果、且 App 是本地单用户
无状态（CLAUDE.md 已认定 permissive 可接受）。scheme + 私网 IP 双校验把风险压到很低。

## 前端 API 客户端（`frontend/src/lib/api.ts`）

- `searchOpenAlex(query, max, { apiKey, email })` → `/api/openalex?...`
- `searchSemanticScholar(query, max, { apiKey })` → `/api/semantic_scholar?...`
- 保留 `pdfUrl(id)`（arXiv）；新增 `pdfUrlForOa(url)` → `/api/pdf-url?url=${encodeURIComponent(url)}`。

## LLM 工具 + 分发 + 系统提示（`llm.ts`、`ChatView.tsx`）

- 把静态 `SEARCH_TOOLS` 改为 `buildSearchTools({ openalex, s2 })`，按启用情况返回
  `[search_arxiv, search_openalex?, search_semantic_scholar?, web_search]`。
- `runConversation` 每次按 settings 构建工具列表；新增两个分发分支，照 `search_arxiv` 模式：
  调 api → `ui:{papers}` → 推 tool 消息（送模型的截断到 8 条）。
- 源出错（429/网络）：tool 消息回 `"openalex search failed (rate limited); try search_arxiv"`，
  让模型自然回滚 arXiv，不打断循环。
- `GENERAL_SYSTEM_PROMPT` 动态拼接可用源说明，保留「arxiv.org 链接渲染为 in-app 卡」那句。
- `PAPER_SYSTEM_PREAMBLE` 把 "arXiv paper" 泛化为 "paper"，工具说明同步。

## 卡片渲染与点击路由（`PaperCard.tsx`、`ChatPanel.tsx`）

- `PaperCard` meta 行加源徽章（arxiv/openalex/S2）；CTA 文案随可打开性变：
  in-app → "Click to preview PDF →"，外链 → "Open externally →"。
- `onOpenPaper` 改签名收 `Paper` 而非裸 id，按优先级分支：
  1. 有 arXiv-id → `db.savePaper(paper)` 种入元数据 → `navigate(/paper/<arxiv-id>)`，
     PdfViewer 走 `/api/pdf/<id>`。
  2. 无 arXiv-id 但有 `oa_pdf_url` → `db.savePaper(paper)`（含 `oa_pdf_url`）→
     `navigate(/paper/<doi-or-source-id>)`，PdfViewer 经 `pdfUrlForOa` 走 `/api/pdf-url`。
  3. 都没有 → `window.open(external_url, "_blank")`，不进 in-app。
- PdfViewer/PaperView 从种入的 IDB 记录读 `oa_pdf_url` 来决定走哪条代理（PaperView 已有
  `db.getPaper` 调用，顺势传给 PdfViewer）。
- 顺带修一个现有缺口：现在从搜索结果点 arXiv 卡，PaperView 里元数据是空的（只有
  `📄 <id>` 回退）。种入元数据后 arXiv 也显示真标题/作者/摘要——同一段代码、零额外成本。

## 设置 UI（`SettingsView.tsx`）

在 Appearance 之后加 `<h2>Search sources</h2>` 节：

- arXiv：只读「always on」一行，让用户明白兜底。
- OpenAlex：启用开关 + API key（password，可选）+ email（可选，polite pool）+
  外链 `https://openalex.org/settings/api` + 一句限速提示。
- Semantic Scholar：启用开关 + API key（可选）+
  外链 `https://www.semanticscholar.org/product/api#api-key` + 限速提示。

## 错误处理与边界

- 源 429/出错 → 模型回滚 arXiv（见 LLM 分发节）。
- OA PDF 拉取失败（404/付费墙）→ 代理 502，PdfViewer 报错并给 `external_url` 跳转按钮。
- 同时有 arXiv-id 和 `oa_pdf_url` → 优先 arXiv 路径（更稳）。
- 空查询 → 400（同 arXiv）。
- 私网/非 http(s) URL → 代理 400。

## 测试 / 验证

- `npm run typecheck`（唯一门）。
- Vitest 单测（纯函数好测）：`resolvePaperId(paper)` 稳定 id、`buildSearchTools` 按配置出工具、
  OpenAlex 倒排索引重建摘要、卡片点击路由决策函数。
- E2E（Playwright rig）：扩展 `tools/mock_llm.py` 识别 `search_openalex`/`search_semantic_scholar`
  工具调用并返回 canned 结果（含一篇 OA + 一篇纯外链）；新驱动用 `page.route` 拦截
  `/api/openalex`、`/api/semantic_scholar`、`/api/pdf-url` 返回 canned JSON/PDF，断言源徽章、
  OA 卡点击进预览、外链卡点击跳转。无需真实网络/key。
- 手测：填真实 key 验证 live 搜索 + OA 打开。

## 不在本次范围

- `arxiv_id` → `paper_id` 全仓重命名 + IDB v3 迁移（方案 B，推迟）。
- 扇出合并多源搜索（方案 C，推迟）。
- 引用数、S2 推荐、OpenAlex 概念分面等富字段。
- `websearch.py` stub 接线（无关，不动）。
- 非 OA 付费墙 PDF：仅外链（法律/技术上都不能拉）。
