# seo-diagnosis-agent 工作台开发交接

2026 年 11 月 27 日

七位专员已完成注册化。复诊前准备清单、OpenAI / DeepSeek 文本模型选择、逐工具公开 Skill 和课程联查恢复均已验收。下一轮从当前工作树继续，待处理发现按用户影响排序，优先处理 PubMed 查询的出站范围。[注册化验收](specialist-registry-verification.md) · [工作台审查](workbench-audit.md)

## 接手基线

本地 `main`、跟踪引用 `origin/main` 和实际远端 `main` 均为 `2af90efe3975a8bd695850d317d495f05765d5ed`（`feat: refine specialist capabilities and workbench workflows`）。交付轮的提交和推送数均为零。本轮实现、测试文件和截图保存在当前工作树。接手时同时核对已跟踪差异和未跟踪文件，换副本时一并转移两者。旧交接用于了解需求，Git 状态以重新核对为准。[仓库指引](../AGENTS.md) · [复诊与模型原交接](workbench-followup-openai-handoff.md) · [注册化原交接](specialist-registry-handoff.md)

```bash
git status --short --branch
git log -1 --oneline
git diff
git ls-files --others --exclude-standard
```

## 已交付和代码入口

| 功能 | 当前行为 | 从哪里接手 |
| --- | --- | --- |
| 专员注册与分派 | SEO / GEO、医药、学习陪练、报考备考、复诊、商旅和采购由各自业务包定义，应用集中登记。稳定专员 ID 继续用于历史，前端从事件与目录读取显示资料。 | [应用装配](../src/agent/workspace/bootstrap.py) · [注册契约](../src/agent/workspace/registry.py) · [工作台编排](../src/agent/workspace/team.py) · [前端事件状态](../frontend/src/state/agentState.ts) |
| Agent 能力 | `GET /api/v1/chat/tools` 用本次会话的实际工具工厂投影工具、状态和 Skill。工具内展示各自用法，第二层展开批准公开的静态正文。无版本响应按已知绑定取兼容快照。 | [目录 API](../src/api/routes/workspace/conversations.py) · [公开投影](../src/api/routes/workspace/directory.py) · [目录组件](../frontend/src/components/SpecialistDirectory.tsx) |
| 复诊前准备清单 | 一份可读资料即可在资料区生成草稿，条目可追查文件和解析行。确认当前版本后保存资料包和有记录依据的待办。明确的复诊整理及恢复可在本地运行。用户可全局新建对话，从历史重开原任务，或在查看同一会话的旧 Run 时点「返回当前任务」。 | [资料区](../frontend/src/components/FollowupPanel.tsx) · [资料 API](../src/api/routes/workspace/followup.py) · [复诊工作流](../src/followup_agent/workflow.py) · [资料存储](../src/followup_agent/store.py) |
| 文本模型 | 主对话、通用 Agent 和独立 SEO 文本流程共用模型选择。自动模式优先使用已配置的 OpenAI，也支持仅配 DeepSeek 和显式指定。数据源显示生效和待重启值，运行详情显示本次上游回执中的实际型号。 | [配置](../src/core/config.py) · [模型工厂](../src/agent/providers/factory.py) · [SEO 模型入口](../src/seo_agent/runtime.py) · [数据源页面](../frontend/src/pages/DataSourcesPage.tsx) |
| 课程联查与恢复 | 医药先核实公开考纲并写入 `public_rule_verified`。SEO 随后读取用户指定的完整课程页。最终核对仅使用公开条款和页面证据，该轮工具列表为空。恢复沿用同一应用 Run、SEO 执行 ID 和两份成功工具回执。 | [接力编排](../src/agent/workspace/team.py) · [SEO 专员](../src/seo_agent/specialist.py) · [检查点和回执](../src/agent/harness/store.py) |

## 扩展与恢复约束

新增通用专员时，业务包定义 `SpecialistDefinition`，声明稳定 ID、展示资料、派单说明、工具工厂、执行指令与获准公开的 Skill。将定义加入 `bootstrap.py` 的注册元组一次。省略自定义 `execute` 即走通用执行器。隔离验收使用「档案核验」样例，它走通派单、补编号、历史、Trace 和能力页，复用了正式七位专员的前端生产构建。[通用执行器](../src/agent/workspace/execution.py) · [档案样例](../tests/agent/registered_archive_fixture.py) · [登记步骤](engineering.md#登记通用专员)

PromptOps 的 `workspace.route.v9.<digest>` 路由契约来自注册定义。登记变化后，应用初始化相应契约键。自定义部署须使用与该键一致的输出 Schema。未知专员路由进入澄清。撤下专员后，历史仍可读取，旧等待任务的有效补充先保存，继续执行返回 409。排查时区分 `run_id`、`agent_run_id`、`request_id` 和工具调用 ID。同一应用 Run 可对应多个阶段的 Trace，普通 Run 详情含累计事件。[路由契约](../src/agent/workspace/routing.py) · [普通对话 API](../src/api/routes/workspace/conversations.py) · [运行中心](../frontend/src/pages/ObservabilityPage.tsx)

能力目录正文由 `PublicSkill.publish()` 及工具行明确关联的公开共用指令提供。复诊原始资料仅通过受限资料 API 使用，凭据和真实运行数据留在本地。SEO 可读网页、整理建议、生成工作台预览和只读复检，站点方负责修改与发布。与任务无关的地址、电话、作者、日期等缺项直接省略。文本失败按所选服务报告，GEO 引用、生图和专业数据源使用各自的客户端。[公开能力契约](../src/agent/workspace/registry.py) · [工程说明](engineering.md)

## 复跑和演示

本机启动按 README 准备 `.env.local` 和 PostgreSQL。以下命令从仓库根目录运行，`TEST_DATABASE_URL` 指向专用 PostgreSQL 测试库。测试使用合成输入和上游替身。[本地启动](../README.md#本地启动) · [测试库设置](engineering.md#测试)

```bash
.venv/bin/python -m pytest -q tests/agent/test_text_model_integration.py -k one_registration
.venv/bin/python -m pytest -q tests/agent/test_workspace_scenarios.py tests/agent/test_workspace_chat.py
npm --prefix frontend test -- --testTimeout 15000
npm --prefix frontend run lint
npm --prefix frontend run build
```

优先演示课程联查。从「新建对话」发送「请用医学人文考纲核对课程页，只列出适用范围和出处。」刷新等待页后补完整课程链接，再从运行中心核对医药与 SEO 两段和原应用 Run，最后从历史打开结果。503 注入及两份回执复用由场景测试重现。浏览器实测课程页只读一次，核对模型尝试两次，最终显示公告及课程页出处。能力页可重点核对商旅「查询高铁与航班」和医考报名的业务正文。[桌面课程结果](screenshots/specialist-registry/desktop-course-result.jpg) · [失败阶段 Trace](screenshots/specialist-registry/desktop-course-trace.jpg) · [商旅公开 Skill](screenshots/specialist-registry/desktop-travel-approved-skill.jpg)

全量后端 775 项、前端 16 个文件 127 项通过，lint、生产构建和 `git diff --check` 通过。Chromium 159.0.8154.6 在桌面 1440 × 960 与手机模拟 390 × 844 共通过 14 项，含正式装配 12 项与隔离档案装配 2 项。浏览器连接本地 FastAPI、SSE 和隔离数据库。此前单独用合成短请求实测真实 OpenAI，`gpt-5.4-mini` 流式返回 `READY`，实际型号为 `gpt-5.4-mini-2026-03-17`。旧记录兼容证据来自存储、API 和组件回归，真实用户数据库的原位升级仍待验证。[验收结果和 16 张截图](specialist-registry-verification.md) · [真实 OpenAI 验证](workbench-audit.md#复诊和模型阶段的回归与-chromium)

## 待处理问题

| 优先顺序 | 问题与接手验收 |
| --- | --- |
| 1 | PubMed 出站范围。当前查询字符串可直接进入 PubMed 的 `term`。用合成私有标记编写出站回归，断言上游只得到允许的公开主题，结果仍提供可查的 PMID 和出处。[PubMed 客户端](../src/healthcare_agent/pubmed.py) |
| 2 | 等待期间改题和否定。商旅等待时发送「取消出差，改查湖北初中数学教资」，确认保留旧证据并停止原商旅取证。医考报名请求排除 PubMed 时，PubMed 工具调用数为零。[编排](../src/agent/workspace/team.py) · [路由](../src/agent/workspace/routing.py) |
| 3 | 附件与网页复合取证。带 `document_ids` 的创建优先进入资料问答。用合成附件和公开课程页验收实际读取范围、两种证据各自可追溯，以及公开网页请求仅携带必要的网页读取参数。[创建 API](../src/api/routes/workspace/conversations.py) · [资料上下文](../src/agent/workspace/documents.py) |
| 4 | 首次创建回执丢失后重复运行。模拟服务端成功创建、浏览器丢失响应，确认重试按稳定创建标识找回原 Run，用户主动另开时才生成新 Run。[对话页](../frontend/src/pages/ChatPage.tsx) · [回执存储](../src/agent/harness/store.py) |

审查报告还记录部分交付状态、参考链接触发目标选择、新旧分享图意图、任务查找窗口和独立数据源排查。复诊的并行双确认和导入竞态也待补验收。[完整发现与验收条件](workbench-audit.md#待处理发现按用户影响排序)
