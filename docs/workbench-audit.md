# AI Agent 工作台审查与验收

2026 年 11 月 26 日 · seo-diagnosis-agent

复诊改为可从一份资料开始的准备清单，用户在资料区完成导入、整理和版本确认。主对话、通用 Agent 与独立 SEO 文本流程已接通工作台选择的文本服务。待处理发现中，优先级最高的是公开文献查询的外发边界、等待中的改题、复合附件任务的范围，以及首次创建回执丢失后的重复运行。

七位专员完成注册化后的课程联查、单点登记及全量回归见[工作台专员注册化验收](specialist-registry-verification.md)。

## 待处理发现，按用户影响排序

### 1. PubMed 查询需要在请求前校验外发范围

从「新建对话」请求按公开主题查 PubMed 题录，在合成输入中加一段本来只供本地理解的个案备注。源码核对显示，医药 Skill 要求排除病历、身份和联系方式，`LiteratureQuery.query` 仍允许任意查询文本。如果模型把备注填入该字段，PubMed 客户端会将它直接用于 `term`。影响是公开查询有机会带出个案信息。验收用上游替身记录请求，确认只发送公开主题、合成私有标记未出现在请求中，结果仍给出可点开的 PMID 和出处。 [医药 Skill](../src/healthcare_agent/skills.py) · [PubMed 请求](../src/healthcare_agent/pubmed.py)

### 2. 等待中的改题和否定指令可能落到原业务

用户在商旅等待出发地时回复「取消出差，改查湖北初中数学教资」，目标是停下行程并改查报名和考试。源码核对显示，专员自己的补充回执通常沿用保存的分派，商旅解析器继续把答复当作行程条件。同类问题出现在新对话「不查 PubMed，只查湖北医师资格报名条件」，关键词匹配优先命中 `PubMed`，可能覆盖模型选中的报名 Skill。验收时原证据仍可回看，旧商旅不再取证，改题有清晰的专员反馈，并且该医考输入没有 PubMed 工具事件。课程补网址时改问医药的既有路径已通过 API 回归。 [路由与回执](../src/agent/workspace/team.py) · [医药 Skill 选择](../src/agent/workspace/routing.py) · [课程改题回归](../tests/agent/test_workspace_scenarios.py)

### 3. 复合附件任务可能只执行资料问答

从新建对话选中一份本地合成资料，再提交「按附件的课程要求对照指定公开网页，给出 SEO 建议」。用户需要交叉核对两种来源。源码核对显示，含 `document_ids` 的创建直接进入资料问答，先于常规网页分派。已进入普通等待后，附件选择也不可用。用户可能只收到文件解读，以为已检查网页。验收应在结果中明确本轮实际读取范围，让文件片段与公开网页分别可追溯，并验证公开网页请求未携带私有文件内容。 [创建分支](../src/api/routes/workspace/conversations.py) · [文档上下文](../src/agent/workspace/documents.py) · [附件选择和等待](../frontend/src/pages/ChatPage.tsx)

### 4. 首次创建回执丢失后重试可能多建一个 Run

用户发送任务后，模拟服务端已创建记录而浏览器未收到首次 `POST /api/v1/chat/runs` 响应，再从恢复出来的草稿发送。源码核对显示，前端会保留失败草稿，重试发起新的创建请求。服务端没有跨这两次请求复用同一创建结果的标识，任务可能重复取证、计费并产生两条记录。验收以首次发送的稳定标识查回原 Run，重复发送能恢复原流，用户主动另开任务时才生成新 Run。补充接口已有独立请求 ID 和持久回执，顺序重放及恢复已有回归。 [发送与失败恢复](../frontend/src/pages/ChatPage.tsx) · [创建和补充 API](../src/api/routes/workspace/conversations.py) · [回执持久化](../src/agent/harness/store.py)

### 5. 已交付、待选择和未交付在状态上混用

用户只问城市天气时，让模型按商旅处理并仅抽出目的地。源码会先取近期天气，再按出行或住宿条件进入等待。普通 API 的合成出差场景验证了「有近期天气并等待行程补充」；天气单项的具体文案仍属源码核对。采购比价回归验证了完整报价小计已显示、原 Run 仍等用户补充或结束。SEO 页面文案可用而生图不可用时，源码的工具结果可标 `workflow_status=partial`，执行器的常规完成状态可能仍显示「已完成」。验收应让天气单项和已满足的比价正常收尾，将可选比较或保存留给用户，将所请求但未生成的图片标为部分交付，并使结果卡、任务卡和 Trace 反映同一事实。 [商旅等待](../src/travel_agent/workflow.py) · [采购结果](../src/procurement_agent/workflow.py) · [分享图覆盖范围](../src/seo_agent/workflows/share_image.py) · [工作流结果](../src/seo_agent/workflows/execution.py) · [运行完成](../src/agent/runtime/executor.py)

### 6. 关键词研究的参考地址可能触发选目标页

从空白对话提交「只研究一元一次方程练习的关键词，下面的链接仅供参考」。源码核对显示，即便规划选择 `needs_page=false`，遇到 URL 候选且没有当前目标时，`select_page` 也可能要求用户指定页面。主题研究会因无关的页面选择而停住。验收用可用的合成关键词源，主题证据直接交付，参考 URL 不触发目标确认。只有明确要求检查指定页面时才读取正文。 [目标选择](../src/agent/workspace/routing.py) · [工作流分派](../src/agent/workspace/team.py)

### 7. 排除原图可能被解释为退出生图

用户同时要页面文案和一张新分享图时，回复「生成一张新的分享图，不用旧分享图」。源码核对显示，生成与排除逻辑按子句更新偏好，后续的「旧分享图」子句可能把已成立的生成偏好覆盖为省略。影响是收到了文案却漏掉明确要求的预览。验收保留 TDK，按用户确认的范围出一次新预览，将排除限定到旧图。需要发布的 `og:image` 做独立可达性核对，站点方在自己的系统中修改和发布。地址、电话、作者、日期等非必要缺项直接省略。 [分享图偏好](../src/seo_agent/workflows/page_suggestions.py) · [生成与预览回执](../src/seo_agent/workflows/share_image.py)

### 8. 任务工作台的查找窗口和任务标题影响找回

用户想找回一项较早的等待任务时，创建超过 50 个已有消息的会话，再用「等待补充」筛选和历史搜索。源码确认任务工作台先取最近 50 个本机浏览器会话，再按 API 的有限结果窗口过滤，趋势与计数来自已加载结果。浏览器实测复诊任务卡能打开保存结果，卡片标题使用通用的「请整理本次复诊前准备清单。」历史则使用用户发起的对话标题。并行复诊任务很难仅凭通用名称区分。验收用超过窗口的合成记录找回较早的 Run，明确计数范围并用日期、状态等本地安全信息协助区分。保持资料内容和访问凭证仅在受限资料区使用。 [任务与历史](../frontend/src/components/WorkspaceActivity.tsx) · [任务 API](../src/api/routes/workspace/conversations.py) · [历史回看截图](screenshots/workbench-audit/desktop-followup-history.jpg)

### 9. 生图和 GEO 引用依赖的排查入口不完整

用户的主对话可用，但分享图或引用样本未产出，打开「数据源管理」想定位缺项。源码核对显示，相应工作流各有 Cloudflare 生成和 GEO 引用客户端，配置目录目前没有对应的独立卡片。OpenAI 文本模型的状态无法解释专用模型、远程引用 Agent 或生成客户端的配置问题。验收在同一页定位生效配置、待重启值及调用失败的对应来源，配置状态只展示所需摘要，外部可用性以各自调用回执为准。 [配置目录](../src/core/source_settings.py) · [数据源 API](../src/api/routes/workspace/sources.py) · [GEO 引用客户端](../src/seo_agent/geo_a2a.py) · [生图客户端](../src/seo_agent/workflows/cloudflare_images.py)

## 本轮改动与代码入口

| 交付 | 行为与代码 |
| --- | --- |
| 复诊前准备清单 | 首页发送示例，可导入 CSV、JSON 或模板文字 PDF，也可载入三份合成资料。一份可读资料即可生成「要带的资料」「已有安排」「要问医生的问题」。依据展开到文件和解析行，影响使用的缺口贴近条目。程序保存当前获确认的草稿版本及有记录依据、尚未记录完成的待办。资料 API 使用访问键与 `private, no-store`。原报告值、比较依据和已保存旧包可查看。工作台已撤下逐条修订。原 `PATCH /records/{record_id}` 的产品调用方是旧表单，该端点与旧样例补充 API 标记为 deprecated，供兼容客户端。旧页面重新确认时需要刷新到新版并提供版本。 [首页](../frontend/src/components/WorkspaceHome.tsx) · [资料区](../frontend/src/components/FollowupPanel.tsx) · [整理规则](../src/followup_agent/materials.py) · [工作流](../src/followup_agent/workflow.py) · [资料 API](../src/api/routes/workspace/followup.py) · [存储](../src/followup_agent/store.py) |
| 复诊等待和恢复 | 本次动作随资料区呈现，正常等待收起底栏。普通问答等待仍可用「开始新任务」，全局「新建对话」和历史保持原入口。查看同一会话的旧 Run 时可返回当前等待或运行中的任务。发起已明确的复诊整理与恢复本地复诊无需文本模型，即使文本配置变化也可保存。 [对话页](../frontend/src/pages/ChatPage.tsx) · [运行入口与版本校验](../src/api/routes/workspace/conversations.py) · [补充回执](../src/agent/harness/store.py) · [能力快照](../frontend/src/services/bundledSkillCatalog.json) |
| 文本模型与配置状态 | 自动模式优先选已填写 Key 的 OpenAI，支持仅 DeepSeek 及显式指定。主对话和通用 Agent 共用流式传输，独立 SEO 文本流程共用选择和结构校验。失败报告选定服务的原因，模型依赖的旧 Run 恢复时校验原服务。GEO 引用采样仍按其专用配置调用，云端生图和专业数据源各走原客户端。数据源区分当前模型与保存后待重启的模型。成功 Trace 从调用回执展示实际型号，失败记录保留所请求的型号与错误码。 [服务选择](../src/core/config.py) · [OpenAI 流式适配](../src/agent/providers/openai.py) · [通用 Agent](../src/api/routes/agent.py) · [SEO 结构化入口](../src/seo_agent/runtime.py) · [数据源页面](../frontend/src/pages/DataSourcesPage.tsx) · [运行中心](../frontend/src/pages/ObservabilityPage.tsx) |

## 七位专员的入口与回归证据

| 专员 | 可从新建对话交付的任务 | 已验证的路径 |
| --- | --- | --- |
| SEO / GEO | 读取网页标题，或取得有出处的页面诊断和改稿 | 普通 API、SSE 与两种浏览器尺寸完成网页读取和模型工具回传。GEO 引用与改稿证据校验在本地替身运行。 [对话测试](../tests/agent/test_text_model_integration.py) · [GEO 测试](../tests/seo_agent/test_geo.py) |
| 医药资料 | 查公开医考规则、院校资料或文献 | 普通 API 回归覆盖查不到院校证据时的反馈，以及医药考纲依据连同来源交给 SEO。PubMed 出站范围见发现 1。能力页两种尺寸实测可读指令。 [场景回归](../tests/agent/test_workspace_scenarios.py) |
| 学习陪练 | 一元一次方程一次一题，提交后判分 | 普通 API 回归覆盖续答、重复判分去重和同会话重练错题。 [场景回归](../tests/agent/test_workspace_scenarios.py) |
| 报考备考 | 查湖北初中数学教资笔试和备考清单 | 普通 API 回归覆盖合并已确认条件和超资料范围说明。 [场景回归](../tests/agent/test_workspace_scenarios.py) |
| 随访与复诊准备 | 由门诊小结或预约等资料生成可回查清单 | 两种浏览器尺寸完成导入或样例、草稿、来源、刷新、版本确认和历史回看。 [场景回归](../tests/agent/test_workspace_scenarios.py) · [手机清单](screenshots/workbench-audit/mobile-followup-checklist.jpg) |
| 商旅行程 | 按日期和范围查交通、住宿、天气并比较 | 普通 API 使用替身验证先交付近期天气、仅查要求的服务、按变化重查和确认保存。 [场景回归](../tests/agent/test_workspace_scenarios.py) |
| 购物与采购 | 对照两个商家报价或核对到货 | 普通 API 使用合成报价验证缺采购 Key 时能补录、按数量重算、标明未确认费用及核对到货。 [场景回归](../tests/agent/test_workspace_scenarios.py) |

## 复诊和模型阶段的回归与 Chromium

后端 143 项通过，使用隔离 PostgreSQL 14 和上游响应替身。覆盖资料解析与旧资料包序列化、旧修订兼容、版本冲突、保存后回执前故障恢复、补充回放、无模型复诊、复诊资料在运行详情、SSE 与 Trace 中的隔离，以及专员续跑和 SEO 独立文本入口。模型覆盖仅 OpenAI、仅 DeepSeek、双 Key 自动和显式 DeepSeek、缺配置、认证失败、无权限、限流、额度不足和超时。失败测试校验无其他文本接收方请求。 [复诊与专员](../tests/agent/test_workspace_scenarios.py) · [旧资料包](../tests/agent/test_followup_materials.py) · [模型与错误](../tests/agent/test_text_model_integration.py) · [配置状态](../tests/agent/test_workspace_sources.py)

前端定向回归 4 组 62 项通过，覆盖复诊面板、工作台对话、产品入口与运行中心。Linux 并行回归中，能力页跨多次交互的用例达到默认 5 秒时限，使用 `--testTimeout 15000` 完整重跑通过。生产构建和 lint 已通过。 [复诊](../frontend/src/__tests__/FollowupPanel.test.tsx) · [对话](../frontend/src/__tests__/ChatSeoAgentIntegration.test.tsx) · [工作台](../frontend/src/__tests__/ProductApp.test.tsx) · [运行中心](../frontend/src/__tests__/ObservabilityPromptOps.test.tsx)

两种尺寸均使用 Chromium 159.0.8154.6、前端生产构建、普通菜单和本地 FastAPI、SSE、PostgreSQL。复诊资料、页面及文本模型上游均为合成或替身。Trace 与数据源截图中的模型名来自该次替身回执与配置。

| 普通入口 | 桌面 1440 × 960 | 手机 390 × 844 |
| --- | --- | --- |
| 新建对话 → 复诊快捷任务 → 确认保存 → 历史 | 载入三份合成资料，展开原始文件行，刷新后确认相同版本，保存两条有资料依据的待办。重复提交复用同一保存结果。 [等待](screenshots/workbench-audit/desktop-followup-waiting.jpg) · [文件与解析行](screenshots/workbench-audit/desktop-followup-source.jpg) · [清单](screenshots/workbench-audit/desktop-followup-checklist.jpg) · [历史](screenshots/workbench-audit/desktop-followup-history.jpg) | 导入一份合成门诊 CSV，清单仍可整理和保存。无预约记录，保存零条待办。同样覆盖刷新与重放。 [等待](screenshots/workbench-audit/mobile-followup-waiting.jpg) · [清单](screenshots/workbench-audit/mobile-followup-checklist.jpg) |
| 运行中心 → 旧 Run → 返回关联会话 | 查看旧任务后点「返回当前任务」，回到同一会话的等待 Run。全局新建后从历史打开同一 Run 并能载入样例。 | 相同路径通过。 [返回当前任务](screenshots/workbench-audit/mobile-old-run-return.jpg) |
| 新建对话 → OpenAI 合成网页标题任务 → 运行中心 → 数据源管理 | 只配置 OpenAI。收到流式结果，网页元信息回执以原工具调用 ID 返回，Trace 显示 `openai-actual-snapshot`。保存模型别名后分列当前值和重启后值，Key 输入留空且响应无值。 [Trace](screenshots/workbench-audit/desktop-model-trace.jpg) · [配置](screenshots/workbench-audit/desktop-model-sources.jpg) | 相同链路和状态核对通过，数据源页无横向溢出。 [Trace](screenshots/workbench-audit/mobile-model-trace.jpg) · [配置](screenshots/workbench-audit/mobile-model-sources.jpg) |
| Agent 能力 → 复诊和医药工具 | 实际 `/api/v1/chat/tools` 返回七位专员及复诊三个工具的 Skill 绑定。医药两级指令可用键盘展开，正文 1041 / 1057 px。 [指令](screenshots/workbench-audit/desktop-capability-instructions.jpg) | 两级指令正文 300 / 316 px，长文本换行且无横向溢出。 [指令](screenshots/workbench-audit/mobile-capability-instructions.jpg) |

真实 OpenAI 最小验收使用服务端已有配置，通过官方 Chat Completions 对 `gpt-5.4-mini` 发送一条合成单词应答请求。流式返回 `READY`，上游型号 `gpt-5.4-mini-2026-03-17`，正常结束。此次请求仅含合成文本。工具回传、错误与配置矩阵的证据来自上述替身回归。 [OpenAI Provider](../src/agent/providers/openai.py)

旧版资料包验收覆盖持久化形状与工作台组件读取，新保存资料包已走普通历史入口端到端验证。完全并发的双确认与导入竞态仍适合单独验证，当前已有跨重启、同请求重放、资料变更后的旧版本拒绝与行锁二次检查。 [旧包组件](../frontend/src/__tests__/FollowupPanel.test.tsx) · [回执回归](../tests/agent/test_workspace_scenarios.py) · [持久化校验](../src/followup_agent/store.py)

## Git 基线

从本地 `main` 的 `2af90ef` 接手。远端首次核对为 `de72db6`，专员注册化交付前查询 `refs/heads/main` 得到 `2af90ef`，与本地 HEAD 一致。本轮改动保存在当前工作树。 [原交接要求](workbench-followup-openai-handoff.md) · [专员注册化验收](specialist-registry-verification.md)
