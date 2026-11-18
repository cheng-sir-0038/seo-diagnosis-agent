# SEO Diagnosis Agent

选一个网站，直接说你想查什么。DeepSeek 根据问题和取回的证据选择下一步工具，能查页面问题、研究关键词和排名页、拟定 SEO 建议。配置 Cloudflare Workers AI 后，也能在对话里生成页面分享图。

## 真实对话

用户准备更新首页在搜索结果和分享链接上的文案，希望先拿到一份可评审的草稿。DeepSeek 根据这句需求选择页面建议流程。读到页面后，Agent 发现站点主体和配图需要确认，于是保留证据，等待用户决定。用户选择「先用现有信息生成」，同一次运行继续，交付标题、摘要、分享信息和 JSON-LD 草稿，并标出待确认事项。[完整对话](docs/screenshots/07-page-suggestions.png) · [执行详情](docs/screenshots/11-execution-details.png)

```mermaid
flowchart TD
    Read["DeepSeek 选择页面建议流程<br/>Agent 读取页面源码"]
    Pause["页面主体和分享图待确认<br/>保存已读证据，暂停运行"]
    Choice(["用户点击「先用现有信息生成」"])
    Resume["继续同一次运行<br/>复用已读页面证据"]
    Draft["校验 JSON Schema 和引用<br/>返回草稿，标出待确认项"]
    Read --> Pause --> Choice --> Resume --> Draft
```

[首页体检](docs/screenshots/19-homepage-priority.png) · [关键词机会](docs/screenshots/04-keyword-opportunities.png) · [已有排名页优化](docs/screenshots/05-ranking-pages.png) · [完整对话截图](docs/screenshots/README.md) · [本地启动](#本地启动)

## Agent 如何运行

- DeepSeek 根据问题和已有证据选择工具。代码限制模型轮次、工具调用总数和超时，复用本次运行中重复的成功查询。[对话入口](src/api/routes/seo_agent_chat.py) · [工具循环](src/agent/loop.py)
- 工作流安排取证步骤、校验实际来源和结构化产物。需要人工补充时，运行检查点保存已查到的证据。[工作流代码](src/seo_agent/workflows/) · [检查点与恢复](src/agent/harness/)
- 查询结果和来源紧跟对话结论。「执行详情」默认折叠，展开可查看调用状态、耗时和已取得的参数。配置 Langfuse 后可查看模型和工作流的完整调用树。[观测代码](src/observability/)

```mermaid
flowchart TB
    UI["React 工作台"] --> API["FastAPI"]
    API --> CHAT["SEO Chat：站点上下文、提示词、可用工具"]
    CHAT --> LOOP["DeepSeek + 工具循环"]
    LOOP --> WF["业务工作流：页面 / 搜索 / 关键词 / 草稿"]
    LOOP --> TOOL["只读 SEO 工具"]
    WF --> TOOL
    WF --> CF["Cloudflare Workers AI（分享图）"]
    TOOL --> DATA["Data Facade + Provider Router"]
    DATA --> EXT["页面 HTML / Serper / DataForSEO / PageSpeed"]
    LOOP <--> HARNESS["Harness：事件、检查点、恢复"]
    HARNESS --> RUNDB[("运行与业务 SQLite")]
    LOOP --> OBS["Observability：模型、工具与子步骤"]
    OBS --> OBSDB[("观测 SQLite")]
    OBS -.->|可选| LF["Langfuse Cloud"]
    API --> BIZ["机会 / 审批 / 任务 / 发布 / 监控接口"]
    BIZ --> RUNDB
```

对话中的站点取证只读，建议和生成图保存在本应用。创建执行任务、审批和发布走各自的业务接口。

## 业务场景与现有入口

下面按业务问题说明已有代码能支撑什么；工具组合取决于模型判断、数据源配置和本次查询结果。

| 业务场景与输入示例 | 实际工具或服务 | 产出与当前状态 |
| --- | --- | --- |
| 页面体检：“客户从搜索点进首页，能看懂我们提供什么吗？帮我找出最值得先改的地方。” | `seo_site_audit` → `seo_page_metadata`，按需调用 `seo_page_performance` | 页面证据和优先建议。需要检查速度时可加上移动端性能数据 |
| 页面信息建议：“根据这一页的内容，帮我拟定标题、描述、分享信息和结构化数据。” | `seo_page_suggestions` → 页面事实、模型草稿、引用校验、缺失信息追问 | 给出 TDK、OG、JSON-LD 建议；用户补充后沿用对话继续生成，不修改网站 |
| 页面分享图：“按这页内容帮我生成一张分享图。” | `seo_share_image` → 读取页面、校验主体依据、调用 Cloudflare Workers AI | 配置生图服务后在对话中直接显示图片 |
| 搜索现状：“看看本站的自然流量估算、排名分布和代表关键词。” | `seo_search_diagnosis` → `seo_search_metrics` + `seo_organic_keywords` | 站点指标、有限排名样本和对应页面；只查排名词时使用关键词查询，样本为空时说明数据缺口 |
| 关键词机会：“围绕手冲咖啡，有哪些关键词值得研究？” | `seo_keyword_opportunity` → 联想词、批量关键词指标、输入主题的 SERP | 去重后的候选词和可用指标；已封装为工作流，缺失指标保持未知 |
| 机会生成与入池：显式提交一次机会发现请求 | `OpportunityDiscoveryWorkflow` → 关键词研究、冻结快照、模型候选、证据校验、既有入池服务 | 生成至多 3 个机会并沿用原有评分和审批策略；独立业务接口触发，不是只读 Chat 工具 |
| 已有排名页优化：“哪些已有排名的页面值得进一步检查？” | `seo_ranking_page_optimization` → 排名样本、少量站内 HTML、每页一个代表词 SERP | 默认调查第 4–20 名样本中的 2 个站内页面，输出实际标签、搜索证据和待核实项；不修改页面 |
| 页面变更复检：“按之前的快照，检查 Title、H1 和 Canonical 是否符合目标。” | `seo_page_change_review` → 本站基线、重新读取页面、冻结新快照、逐项比较 | 输出变更明细和验收结论；先显式建立基线，缺少目标值或完整证据时不判定通过 |
| 竞品分析：“谁在和本站竞争这些搜索词？” | `seo_site_diagnosis` 的可选竞品步骤，或 `seo_organic_competitors`、`seo_serp_overview` | 自然搜索竞品、交集指标及指定关键词的搜索结果，不代表完整关键词差距 |
| 站点综合诊断：“检查本站页面、抓取规则和站点地图。” | `seo_site_diagnosis` → 页面体检、robots.txt、sitemap，可补查询搜索表现和关键词机会 | 页面问题证据、站点文件和已完成的检查范围 |
| 已批准机会任务规划：显式提交已批准机会与负责人 | `TaskPlanningWorkflow` → 校验最新审批、生成 PRD 内容、原子写入任务和文档记录 | 创建草稿任务、PRD 和本地 Markdown/PDF；支持幂等重试，不自动派发或发布 |
| 交付审核：提交交付后，按对应 PRD 检查 | [`review_delivery`](src/seo_agent/workflows/task_delivery.py) | 模型生成通过项、失败项和修改意见，再由服务校验并记录审核；通过后仍需走发布规则 |

只读关键词工作流产出研究候选与证据，不会写入业务机会。显式调用机会发现接口后，才会生成并校验 `OpportunityCandidate`、进入既有机会池；两条路径都不会批准机会、创建任务或发布。搜索表现来自第三方数据源，不能据此冒充 GSC/GA4 的实际访问量或完整下降归因。

## 工具与业务工作流

[`src/seo_agent/chat/tools.py`](src/seo_agent/chat/tools.py) 定义了 16 个基础数据工具，并按依赖注册最多 8 个对话工作流。实际可用工具取决于当前数据源和配置。

| 分组 | 工具 | 数据来源 |
| --- | --- | --- |
| 页面 | `seo_page_metadata`、`seo_page_performance` | 页面源 HTML、PageSpeed |
| 搜索表现 | `seo_search_metrics`、`seo_organic_keywords` | DataForSEO |
| SERP 与关键词 | `seo_serp_overview`、`seo_keyword_overview`、`seo_autocomplete` | Serper / DataForSEO；关键词指标需要 DataForSEO |
| 竞品与外链 | `seo_organic_competitors`、`seo_backlinks_list` | DataForSEO |
| 新闻、图片、视频 | `seo_news_search`、`seo_image_search`、`seo_video_search` | Serper |
| 当前不可用于正式对话 | `seo_search_history`、`seo_top_pages`、`seo_site_profile`、`seo_audit_issues` | 当前仅由 pseudo 模式的 Ahrefs 提供 |

业务工作流代码位于 [`src/seo_agent/workflows/`](src/seo_agent/workflows/)：

| 代码 | 入口 | 业务职责 |
| --- | --- | --- |
| [`site_audit.py`](src/seo_agent/workflows/site_audit.py) | `seo_site_audit` | 保留现有入口，读取单页元信息和可选 PageSpeed 数据 |
| [`page_suggestions.py`](src/seo_agent/workflows/page_suggestions.py) | `seo_page_suggestions` | 依据页面和用户补充拟定 SEO 信息，校验引用，追问必要缺项并继续生成建议 |
| [`share_image.py`](src/seo_agent/workflows/share_image.py) | `seo_share_image` | 根据页面证据生成绘图指令和分享图，缺标题时等待补充 |
| [`search_diagnosis.py`](src/seo_agent/workflows/search_diagnosis.py) | `seo_search_diagnosis` | 汇总站点指标与排名关键词，保留当前快照和来源 |
| [`keyword_opportunity.py`](src/seo_agent/workflows/keyword_opportunity.py) | `seo_keyword_opportunity` | 围绕用户主题，组合联想词、候选词指标与主题 SERP |
| [`ranking_page_optimization.py`](src/seo_agent/workflows/ranking_page_optimization.py) | `seo_ranking_page_optimization` | 从排名样本选少量站内页面，核对 HTML、代表词 SERP 和可选性能证据 |
| [`page_change_review.py`](src/seo_agent/workflows/page_change_review.py) | `seo_page_change_review` | 为本站页面建立基线，重新读取同一页面并核对明确的验收值 |
| [`site_diagnosis.py`](src/seo_agent/workflows/site_diagnosis.py) | `seo_site_diagnosis` | 汇总代表页面、robots.txt、站点地图和可用搜索证据 |
| [`opportunity_discovery.py`](src/seo_agent/workflows/opportunity_discovery.py) | `OpportunityDiscoveryWorkflow.execute` | 冻结研究证据、生成候选并校验后，原子执行既有入池和审批请求处理；仅由业务接口调用 |
| [`task_planning.py`](src/seo_agent/workflows/task_planning.py) | `TaskPlanningWorkflow.execute` | 将已批准机会转成草稿任务与 PRD，原子记录结果并按幂等键复用回执；仅由业务接口调用 |
| [`task_delivery.py`](src/seo_agent/workflows/task_delivery.py) | `TaskDeliveryWorkflows.generate_prd` / `review_delivery` | 已有任务 PRD 生成和交付审核流程；通过业务接口调用，不注册为只读 Chat 工具 |

站点综合诊断的实际步骤如下；模型仍可单独选择其中一个流程或基础工具：

```text
seo_site_diagnosis
  ├─ seo_site_audit
  │   ├─ seo_page_metadata
  │   └─ seo_page_performance（仅用户要求性能时）
  ├─ seo_site_resource（robots.txt）
  ├─ seo_site_resource（sitemap XML）
  ├─ seo_search_diagnosis（依赖可用时）
  │   ├─ seo_search_metrics
  │   └─ seo_organic_keywords
  ├─ seo_keyword_opportunity（指定 query 且依赖可用时）
  │   ├─ seo_autocomplete（可用时）
  │   ├─ seo_keyword_overview（一次批量指标查询，可用时）
  │   └─ seo_serp_overview（仅查询输入主题词）
  └─ seo_organic_competitors（仅用户要求竞品时）
```

关键词研究的 `limit` 默认 5、上限 10，主题词和联想词合并去重后受此数量限制；SERP 不会逐个核验所有候选词。综合诊断仅在提供 `query` 时研究关键词，性能和竞品步骤分别由 `include_performance`、`include_competitors` 显式开启。

搜索现状需要站点指标和排名关键词；关键词研究需要 SERP 及联想词或关键词指标中的至少一种。综合诊断可仅用页面和站点文件完成技术检查，DataForSEO、Serper 等数据源按需配置。自然语言对话仍需要 DeepSeek。

技术检查读取受检页面所在源的根路径 `/robots.txt`。站点地图优先使用用户提供的同源地址，其次用 robots.txt 中的首个同源 Sitemap 声明，最后尝试 `/sitemap.xml`。每次最多读取一个地图，展示最多 5 个同源条目；遇到 sitemap 索引只展示子地图地址。文件和页面共用请求超时与站内 URL 校验。报告展示读到的抓取指令样例。

排名页优化需要排名关键词、页面元信息和 SERP 三种 live 能力。`limit` 默认读取 20 条排名样本、最多 50 条；`min_position` / `max_position` 默认 4 / 20；`max_pages` 默认 2、上限 3。相同页面去重，只取站内 HTTP(S) URL，每页查询一个代表词的 SERP；`include_performance=true` 时才检查性能。未找到匹配页只代表本次样本没有匹配，未核验页面不会被描述成已确认的优化问题。

报告通过 `coverage` 记录完成、失败、不可用或跳过，并保留已取得的证据。文件返回 404 或 410 时显示该地址未发现文件；HTTP 错误或 XML 无法解析时对应步骤为部分完成。Google 实际收录保持未检查。源码读取不执行 JavaScript。

页面体检及综合诊断的 `findings` 使用[带 JSON Schema 的页面问题模型](src/seo_agent/workflows/page_findings.py)，记录问题码、受检 URL、观察值、来源和读取时间。前端沿用证据卡片展示。重复 Canonical 或声明 noindex 时标为待核实；HTTP 错误页的标签不作为正常页面的问题证据。

### 页面 SEO 信息建议

在对话中提出“根据这个页面拟一份标题、描述、关键词、分享信息和结构化数据建议”即可；用户不需要选择工具或填写参数。此流程只需页面可访问和 DeepSeek 配置，不需要搜索数据服务的 Key。

流程读取当前页的标签、正文摘录和已有 JSON-LD，由模型理解页面实际用途并提出草稿。输入与输出由 [`PageSuggestionInput`、`PageSeoDraft`](src/models/page_suggestions.py) 的 JSON Schema 定义，JSON-LD 直接返回对象。模型使用 JSON Output，后端再校验字段格式、引用原文和 URL 来源，生成 TDK、OG、JSON-LD 与 HTML 片段。

缺少必要信息时进入 Human-in-the-loop：每次询问最多三个具体问题，将页面证据和待答问题保存到检查点，运行变为 `waiting_for_input`，停止继续调用模型。用户在同一对话中回答后，前端通过 `POST /api/v1/seo-agent/chat/runs/{run_id}/input` 提交符合 [`HumanInputAnswer`](src/agent/models.py) Schema 的回答，恢复同一 Run、Task 和 Thread；相同页面复用已保存的证据，调用预算不重置。回答、事件和检查点原子保存，同一请求重复提交复用回执，冲突回答拒绝；服务重启不会自动跳过等待。这个补充步骤不代表机会审批。

生成中刷新页面时，前端使用保存的 Run ID 和本轮起始事件编号，补回本轮进度并继续接收结果。同一个 Run 的各条事件连接独立读取事件。

已有有效草稿时，用户也可选择“先用现有信息生成”（`action=use_available`）：未确认内容列为待补充，不再阻塞本轮建议；事实与格式校验仍执行。没有有效草稿的必要补充不能用这个选择跳过。

页面原文和用户补充分开标记。没有依据的价格、评分、日期、作者或图片不自动补齐；不能确认结构化类型时先保留可确定的文案并追问。没有执行 JavaScript，正文最多读取 6000 字符，对话补充最多使用最近 5 条用户输入、每条 2000 字符。HTML 片段保留在结果中，JSON-LD 在卡片展示；提示词、模型调用和引用校验可在运行记录中查看。

结果始终是待人工确认的建议，不修改外部网站、Canonical、审批或发布记录。已有 JSON-LD 需人工确认合并，不能直接覆盖。运行时只做 JSON 结构与来源校验，落地前需使用 [Schema.org 验证器](https://validator.schema.org/)和适用的 [Google 富结果测试](https://search.google.com/test/rich-results)另行验证；语法通过不保证符合 Google 展示规则。站点名称不适用富结果测试，需按 [Google 站点名称规则](https://developers.google.com/search/docs/appearance/site-names)核对。[Google 不使用 Meta Keywords 参与收录和排名](https://developers.google.com/search/docs/crawling-indexing/special-tags#unsupported)，关键词仅作为主题文案建议。

### 页面分享图

在对话中说「按这页内容帮我生成一张分享图」即可，省略地址时取当前站点首页。工作流从页面的 `og:title`、Title、H1 中依次选取标题，描述取 `og:description` 或 Meta Description。标题都缺失时，同一次运行等待用户输入，恢复后复用页面证据。

DeepSeek 根据页面内容和用户要求选取主体及构图。代码校验 JSON Schema 和引用原文，缺少主体依据时请用户补充。Cloudflare Workers AI 生成 1216 × 640 的图片，图片保存在 SEO 业务 SQLite 文件旁的 `*.share-images` 目录，并在对话中与页面文案一起展示。绘图提示词可在运行的模型调用记录中查看。上游失败时保留已取得的页面证据。[对话截图](docs/screenshots/18-share-image-brief.png) · [工作流](src/seo_agent/workflows/share_image.py) · [Cloudflare 接入](src/seo_agent/workflows/cloudflare_images.py) · [业务测试](tests/seo_agent/test_share_image.py)

### 机会生成与入池

以咖啡教程站为例，想找下一篇值得写的手冲咖啡教程。先为本站的 `OPPORTUNITY_SCAN` 计划创建一条 `RUNNING` 状态的扫描记录，再调用下面的接口研究这个主题，将通过校验的候选加入机会池。

```text
POST /api/v1/seo-agent/scan-runs/{run_id}/discover-opportunities
```

```json
{
  "query": "pour over coffee",
  "changed_by_actor_id": "有效操作人 ID",
  "default_approver_actor_id": "可选的有效审批人 ID",
  "idempotency_key": "本次请求的唯一键",
  "max_opportunities": 3
}
```

站点范围由扫描记录决定，不能在请求中替换 `site_id`。流程复用关键词研究，将结果冻结到现有 SQLite 证据快照库，再由 DeepSeek 生成结构化候选。代码校验市场、关键词、竞品 URL 和快照引用；搜索量、难度使用首个关键词的实际指标，缺失时保持未知。模型可以明确返回空候选列表。

SERP 返回未解析的 Google 跳转地址时，原始快照照常保留，但跳转地址不作为竞品页面入库，竞品数量保持未知。

通过校验的批次复用现有归一化、去重和评分策略；评分因素由模型提出，最终分数由现有策略计算，不代表已经证实的收益。机会、观测、审批请求、通知队列、扫描完成状态及回执在一个业务事务内提交，中途失败或取消会整体回滚。相同扫描记录下，相同幂等键和请求内容复用已完成回执，不再次生成机会；同一键换内容会被拒绝。失败前的外部取数、模型调用或快照保存仍可能在重试时发生，不能据此承诺外部服务只调用一次。

成功落库后扫描记录变为 `COMPLETED`；失败时保持可重试的 `RUNNING`。提供 `default_approver_actor_id` 时，高价值机会沿用原服务创建待审批请求；未提供时只入池等待后续申请。中低价值机会仍进入机会池或观察状态。所有机会创建任务前仍须通过最新审批，接口不会自动批准、创建任务或发布。此流程与 PRD、交付审核共用[结构化模型调用与观测](src/seo_agent/workflows/structured_model.py)。

### 已批准机会 → 任务与 PRD

显式调用 `POST /api/v1/seo-agent/tasks/plan`，不通过只读 Chat 自动建任务：

```json
{
  "site_id": "有效站点 ID",
  "title": "关键词内容优化任务",
  "task_type": "ARTICLE",
  "priority": "HIGH",
  "opportunity_ids": ["已批准机会 ID"],
  "owner_actor_id": "有效负责人 ID",
  "created_by_actor_id": "有效操作人 ID",
  "idempotency_key": "plan-content-001"
}
```

每次至多选择 10 个不重复的本站机会。流程复用建任务服务的审批校验和现有 PRD 提示词：先检查站点、操作人及最新审批，生成并校验 PRD 内容；再在写入事务内重新校验审批，创建 `DRAFT` 任务、PRD、本地 Markdown/PDF 文档记录和完成回执。模型调用不占用数据库写事务，模型失败不创建任务，写入或文档生成失败时业务记录一起回滚。

相同站点下，同一幂等键和请求内容复用已完成结果，不重复建任务或增加 PRD 版本；换内容会被拒绝。跨实例并发请求可能重复调用模型，但事务内再次检查回执，业务记录只写入一份。流程不批准机会、不派发任务、不发布；原有单独建任务、生成 PRD 和交付审核接口继续可用。

### 页面变更复检

SEO Chat 可调用 `seo_page_change_review`；需要 live 页面 HTML 能力和已启动的本地快照库，无额外第三方 Key。先要求 Agent 为目标页面建立基线，工具参数为：

```json
{"action": "capture_baseline", "url": "https://example.com/guide"}
```

工具返回 `baseline_snapshot_id`，此时 `review_status=baseline_recorded`，只表示基线已保存。页面由人工或其他已授权流程修改后，再提供该 ID 和明确的目标值：

```json
{
  "action": "compare",
  "baseline_snapshot_id": "上一步返回的快照 ID",
  "expected": {
    "title": "Content Guide",
    "h1s": ["Content Guide"],
    "canonical_url": "https://example.com/guide"
  }
}
```

复检强制重新读取同一请求 URL，校验基线属于本站且为完整、成功的 `page_html` 证据，保存新快照并展示 Title、Description、H1、Canonical 的前后差异。`expected` 支持 `title`、`meta_description`、`h1s`、`canonical_url`，只逐项精确匹配显式提供的值；`canonical_url: null` 表示预期没有该标签。未提供目标的字段只展示差异，不擅自设定修改目标。

`review_status` 区分 `passed`、`failed`、`insufficient_evidence`；缺少验收值、采集失败或证据不完整时不会判定通过。旧快照不改写，复检不会修改网页或证明收录、排名提升。Langfuse 可查看基线读取、页面工具、新快照和逐项比较；任务规划则记录审批校验、实际 PRD 模型输入输出、原子写入及回执复用。

## 执行控制与恢复

以下默认值由代码执行约束，可通过环境变量调整，见 [`.env.example`](.env.example) 和 [`Settings`](src/core/config.py)。

| 约束 | 配置项 | 默认值 |
| --- | --- | --- |
| 模型循环轮次 | `AGENT_MAX_ITERATIONS` | 8 |
| 循环中的外层工具调用次数 | `AGENT_MAX_TOOL_CALLS` | 12 |
| 单次工具超时 | `AGENT_TOOL_TIMEOUT` | 15 秒 |

迭代预算的最后一轮禁用新工具，转入总结；同名同参的成功工具结果优先复用，随后转入总结。工具次数超限会使运行失败，工具超时则以失败结果交回模型。总结提示词要求说明结论、证据和数据限制，但没有强制校验回答格式或事实正确性。

一次组合工作流在外层计作一次工具调用，内部可能执行多个数据请求，次数不等于第三方接口用量或费用上限。内部步骤受候选数量、固定步骤范围和共享超时控制，实际调用会单独记录 span。

[`Harness`](src/agent/harness/) 保存事件和关键检查点，支持 SSE 重连与服务重启后恢复。已写入检查点的结果可复用；工具执行后、检查点落盘前中断，恢复时可能再次读取。诊断工作流内部子步骤共享外层调用超时，没有独立检查点。

## 业务操作与审批

- [机会管理](src/seo_agent/opportunities/)接收结构化 `OpportunityCandidate`，执行归一化、去重、评分和分级。新机会发现接口已串起研究和入池；[定时扫描](src/seo_agent/automation/schedules.py)仍只创建运行记录，尚未自动调用这条工作流。
- 创建任务时，所有关联机会必须属于当前站点，且最新的“将机会加入生产”审批已通过。中低价值机会不会自动发起审批，可通过审批接口手动创建，工作台尚无申请入口。[LangGraph 审批图](src/seo_agent/approvals/graph.py)已独立测试，尚未接入审批接口。
- [任务与交付工作流](src/seo_agent/workflows/task_delivery.py)中的 PRD 生成和交付审核包含模型调用；写入及状态变更由业务服务校验。[HTML 发布](src/seo_agent/publishing/)要求交付已审核、提交内容与审核内容一致，生产发布还需通过对应审批。
- [监控](src/seo_agent/monitoring/)已有计划、结果接入和采集器协议；当前没有真实收录采集器接入，不能描述成已完成自动发布后复检。完整业务链路仍需真实端到端验收。

## 配置与外部服务

按需将配置写入项目根目录的 `.env.local`，修改后重启后端。配置文件位置不随启动目录变化，系统环境变量优先。变量定义见 [`.env.example`](.env.example) 和 [`Settings`](src/core/config.py)；不要提交真实凭据。

| 服务 | 用途 | 配置变量 | 使用前提 |
| --- | --- | --- | --- |
| DeepSeek | 对话中的工具选择、结论整理、页面信息建议、机会候选生成，以及 PRD 生成和交付审核 | `DEEPSEEK_API_KEY`；可选 `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` | 需要 API 账号、有效 Key 和所选模型的可用调用额度；缺失 Key 时模型请求不可用 |
| 页面 HTML | 读取当前站点页面的源 HTML 和元信息 | 不需要第三方 Key | 当前运行环境能够访问目标公网页面，URL 属于当前站点；不执行 JavaScript |
| Serper | Google SERP、联想词、新闻、图片、视频搜索 | `SERPER_API_KEY` | 需要 Serper API 账号、有效 Key 和相应调用额度 |
| DataForSEO | 关键词指标、自然排名、竞品、外链及 SERP | `DATAFORSEO_LOGIN`、`DATAFORSEO_API_KEY` | 分别填写 API Access 提供的 login 和 API password（API 专用密码）；还需相应端点权限及可用余额或额度 |
| PageSpeed | 单页面 Lighthouse 实验室检查 | `PAGESPEED_API_KEY` | 本项目要求配置 PageSpeed API Key，且请求需满足对应 Google API 项目的权限、限制和配额 |
| Cloudflare Workers AI（可选） | 用 FLUX.2 klein 4B 生成页面分享图 | `CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_API_TOKEN` | 同时需配置 DeepSeek。[配置步骤](#cloudflare-workers-ai-生图) |
| Langfuse Cloud（可选） | 将模型和工具运行记录上传到项目 | `LANGFUSE_HOST`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY` | 需要 Langfuse 项目及有写入权限的项目密钥，Host 与项目区域一致；不配置时保留本地记录 |

DataForSEO 的关键词和站点指标分别使用 [Keyword Overview](https://docs.dataforseo.com/v3/dataforseo_labs-google-keyword_overview-live/) 和 [Domain Rank Overview](https://docs.dataforseo.com/v3/dataforseo_labs-google-domain_rank_overview-live/)；可按官方文档核对端点权限与返回字段。

自然排名、站点指标、关键词指标和竞品查询会根据 [DataForSEO Labs 市场和语言目录](https://docs.dataforseo.com/v3/dataforseo_labs/locations_and_languages/) 校验查询范围。支持的组合使用目录中的市场编号和语言代码。不支持的组合会在发起付费查询前提示覆盖范围，工作流保留其余证据。有效组合返回空样本时，结论限定在本次查询的数据范围内。

业务通知与发布还有各自的配置前提：

- [邮件通知](src/seo_agent/notifications/email.py)需要 `SMTP_HOST`、`SMTP_PORT`、`SMTP_SECURITY`、`SMTP_USERNAME`、`SMTP_AUTH_CODE` 和 `EMAIL_FROM_ADDRESS`；邮箱需开通 SMTP 并取得授权码，端口和加密方式应与邮箱服务匹配。
- [本地 HTML 发布](src/seo_agent/publishing/html_site.py)使用 `HTML_SITE_ROOT_PATH`、`HTML_SITE_BASE_URL` 和可选的 `HTML_SITE_CONTENT_SUBDIR`，不需要第三方 Key。发布目录须可写，并由用户配置的站点服务通过对应 Base URL 提供访问；文件落盘不等于线上地址已经可达。

未配置的数据源不进入正式对话的 live 工具列表。DataForSEO 的兼容 pseudo 数据也不会进入正式诊断；页面体检要求性能检查但 PageSpeed 未配置时，会保留页面证据并返回部分完成。

组合工作流可能连续请求多个外部接口；关键词研究按可用数据源安排一次批量指标查询和一次主题 SERP 查询，HTTP 重试还可能增加请求。请按所需流程配置数据源并查看服务方实际用量，`AGENT_MAX_TOOL_CALLS` 不限制工作流内部的接口请求总数。

填入凭据只说明本地配置已提供，不能证明远端已授权、相关 API 已开通或额度足够；这些状态应以实际接口响应为准，不能仅凭 `/health` 的客户端就绪状态判定。

只读工具将错误码同时交给模型和运行记录，区分数据不可用、认证失败、超时和内部异常。空结果或未知异常不代表用户缺少站点授权；DataForSEO 的第三方排名查询不以连接 GSC/GA4 或绑定站点项目为前提。

PageSpeed 检测可能耗时较长，启用时可将 `AGENT_TOOL_TIMEOUT` 设为 `150` 秒，让页面体检有足够时间保留检测结果；模型轮次与工具调用次数预算不随之增加。

### Cloudflare Workers AI 生图

Agent 读取页面，DeepSeek 根据页面内容规划画面，Cloudflare Workers AI 的 FLUX.2 klein 4B 负责出图。先配好 `DEEPSEEK_API_KEY`。[模型文档](https://developers.cloudflare.com/workers-ai/models/flux-2-klein-4b/)

1. 打开 Cloudflare 控制台，进入 **Workers AI → Use REST API**，复制当前账号的 **Account ID**。
2. 点击 **Create a Workers AI API Token**，将账号范围设为当前账号，使用已实测可生图的 `Workers AI Read` 权限。创建后复制 Token。[Cloudflare 创建步骤](https://developers.cloudflare.com/workers-ai/get-started/rest-api/)
3. 将下面两项填入项目根目录的 `.env.local`。使用 Docker 时，通过容器环境变量传入。[Docker 配置方式](#docker-启动后端)

   ```dotenv
   CLOUDFLARE_ACCOUNT_ID=your_account_id
   CLOUDFLARE_API_TOKEN=your_workers_ai_api_token
   ```

4. 重启后端，在对话中输入「按当前页面内容生成一张分享图」。成功后会在回复里直接看到图片和页面文案。[实际对话截图](docs/screenshots/18-share-image-brief.png)

`AGENT_TOOL_TIMEOUT` 覆盖页面读取、画面规划和生图。需要为分享图留出更多时间时，可将它设为 `90` 秒。Workers AI 每天提供 10,000 Neurons 免费额度。[额度与计费](https://developers.cloudflare.com/workers-ai/platform/pricing/)

## 本地启动

需要 Python 3.11+、Node.js 20.19+ 或 22.12+，以及 npm。自然语言诊断至少需要上表中的 DeepSeek 配置，其他数据源按需启用。

macOS / Linux 在仓库根目录运行：

```bash
sh start.sh
```

访问 `http://localhost:5173`，API 文档位于 `http://localhost:8000/docs`。脚本会复用已有虚拟环境和前端依赖；前端依赖变化时，可运行 `npm ci --prefix frontend`。

API 没有登录或鉴权，默认仅供本机使用，不要直接暴露到局域网或公网。

### 可选：启用 HTML 基础扫描

`python-seo-analyzer` 使用独立 Python 环境，避免与主项目的 LangChain 版本冲突。已有隔离环境时直接复用；首次安装：

```bash
.venv/bin/python -m venv .venv-pyseoanalyzer
.venv-pyseoanalyzer/bin/python -m pip install -r requirements-pyseoanalyzer.txt
```

在 `.env.local` 设置 `PYTHON_SEO_ANALYZER_ENABLED=true`，将 `PYTHON_SEO_ANALYZER_PYTHON` 填为该环境 Python 的绝对路径，再重启后端。扫描器分析已经获取的 HTML，补充词数、标题层级、内链和图片等检查；不启用其中的 LLM 分析。未启用时，这些结果保持未知。

### Docker 启动后端

```bash
docker build -t seo-diagnosis-agent .
docker run --rm -p 127.0.0.1:8000:8000 -v seo-agent-data:/app/data seo-diagnosis-agent
```

镜像只包含后端，`seo-agent-data` 卷保存运行数据。容器不会自动读取宿主机的 `.env.local`：需要真实调用时，可创建不提交的 `.env.docker`，每行写 `KEY=value`（值不要加引号），并给 `docker run` 添加 `--env-file .env.docker`。

前端另开终端启动：

```bash
cd frontend
npm ci
npm run dev
```

已在本地验证镜像构建、容器启动、健康接口、工作流路由和隔离 HTML 分析器。未配置可选数据源或 Ahrefs 仍为 pseudo 模式时，`/health` 的总体状态可能为 `degraded`，可查看各数据源状态。

### 可选：Langfuse Cloud

在 `.env.local` 中配置 `LANGFUSE_HOST`、`LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_SECRET_KEY` 并重启后端。Host 需对应项目所在区域；未配置时仍使用本地 SQLite 记录。

云端记录每轮模型输入（包括用户问题和系统提示词）、可见回答、工具选择及输入输出、执行或复用、结束原因、状态、耗时和模型返回的 Token 用量。组合工作流还会展示实际执行的子流程与数据工具，PRD 和交付审核也保留模型调用记录。模型内部推理不在记录中。

记录可能包含站点和业务数据并发送到云端；常见凭据会脱敏，但不能保证识别所有敏感内容，超长内容会被截断。上传在后台进行，网络故障或程序过早关闭可能导致云端缺少记录，启用前的旧记录不会自动回填。

## 目前的边界

- 综合诊断覆盖代表单页、站点文件和有限搜索样本。Google 实际收录、完整搜索历史和整站抓取仍不在检查范围内。机会入池通过独立接口显式触发，完整“诊断 → 机会审批 → 执行 → 复检”仍需真实端到端验收。
- 这是单机 MVP，SQLite 不适合多实例生产部署。[seo-audit-plugin](https://github.com/cheng-sir-0038/seo-audit-plugin) 是独立项目，本仓库尚未接入其 MCP，也不支持 GSC 或 GA4 数据源。

## 测试

页面诊断回归覆盖健康页、缺 Title、重复 Canonical 和 noindex，以及 robots.txt 和 sitemap 读取。复检用例先保存缺 Title 的页面基线，确认目标未达成，再模拟同一页面的标题修复并重新核对结果。以下测试可在本地复跑，无需第三方 API Key。

```bash
.venv/bin/python -m pytest -q -s tests/seo_agent/test_lab_diagnosis.py
```

```bash
.venv/bin/python -m pytest \
  tests/agent tests/agent_evals \
  tests/seo_agent/test_site_audit_workflow.py \
  tests/seo_agent/test_business_workflows.py \
  tests/seo_agent/test_opportunity_discovery_workflow.py \
  tests/seo_agent/test_ranking_page_optimization.py \
  tests/seo_agent/test_task_planning_workflow.py \
  tests/seo_agent/test_page_change_review.py \
  tests/seo_agent/test_page_suggestions.py \
  tests/providers/test_dataforseo_keyword_overview.py -q
```

前端验证：`npm --prefix frontend test`、`npm --prefix frontend run lint`、`npm --prefix frontend run build`；完整后端用例运行 `python -m pytest -q`。

安装 Ruff 后运行 `ruff check src tests`，静态检查覆盖全部后端源码和测试，不依赖第三方 Key。

[`tests/agent_evals/`](tests/agent_evals/)根据实际运行事件检查工具选择、失败和终态；自动化用例使用模拟模型，不代表 DeepSeek 的真实选工具准确率。新增诊断流程见[业务工作流用例](tests/seo_agent/test_business_workflows.py)，关键词接口字段见[DataForSEO 契约用例](tests/providers/test_dataforseo_keyword_overview.py)。其他业务用例可参阅[机会管理](tests/seo_agent/test_opportunities_workbench.py)、[任务与交付](tests/seo_agent/test_tasks_delivery.py)、[发布与监控](tests/seo_agent/test_publishing_monitoring.py)和[自动化集成](tests/seo_agent/test_automation_integration.py)。

配置 DeepSeek 并启动本地后端后，可以用同一组业务问题评测真实运行。将 `<测试站点 ID>` 换成已初始化的测试站点 ID。

```bash
.venv/bin/python -m src.agent.evals.seo_chat --site-id '<测试站点 ID>'
```

报告记录选中的工具、证据来源、需逐字核对的字段、终态、耗时和对话模型 Token。评测需要的工具未注册，或必需工具因上游失败拿不到证据时，记为 `BLOCKED`。加 `--case tdk` 可单独复跑一题。

本项目采用 [MIT 许可证](LICENSE)。
