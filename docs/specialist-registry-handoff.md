# 工作台专员注册化 · 开发交接

接手 `seo-diagnosis-agent` 的多专员工作台。参考 Mastra 的显式注册方式，让每个专员在自己的业务目录定义职责、工具和执行入口，由应用统一登记。先交付 SEO 试迁，跑通派单、能力展示和续跑，再推广到其余专员。最终验收是新增一个使用通用交互的专员时，只需要新增专员定义并在装配处登记一次。

## 接手基线

交接时本地 HEAD 是 `2af90ef`。工作树已有模型适配、复诊场景和前端改动。接手先用 `git status --short --branch`、`git log -1 --oneline` 和 `git diff` 核对现状，保留已有工作。阅读仓库指引、启动方式和相关回归，使用当前工作树的文本模型工厂及复诊流程作为基线。[仓库指引](../AGENTS.md) · [README](../README.md) · [当前模型工厂](../src/agent/providers/factory.py)

Mastra 通过 `new Mastra({ agents: { … } })` 集中装配，内部逐项调用 `addAgent(agent, key)` 接入共享依赖并登记实例，调用方用 `getAgent(name)` 取回。这个项目用 Python 和 FastAPI 装配定义，继续由 LangGraph 编排业务分派和接力。[Mastra 注册文档](https://mastra.ai/reference/core/mastra-class) · [Mastra 核心源码](https://github.com/mastra-ai/mastra/blob/main/packages/core/src/mastra/index.ts) · [按名取回 Agent](https://mastra.ai/reference/core/getAgent)

当前 `WorkspaceTeam` 同时维护 LangGraph 节点映射和多个专员的执行方法。`RouteDecision` 与路由提示直接引用领域字段，目录 API 又单独组装工具和能力档案。前端 `isSpecialist` 使用固定名单，`agentState.ts` 据此校验派单和 Skill 事件。完整迁移要处理这些实际调用方。[工作台](../src/agent/workspace/team.py) · [路由](../src/agent/workspace/routing.py) · [目录 API](../src/api/routes/workspace/conversations.py) · [公开档案](../src/api/routes/workspace/directory.py) · [前端专员名单](../frontend/src/specialists.ts) · [事件处理](../frontend/src/state/agentState.ts)

## 注册契约

建议把 `SpecialistDefinition` 和 `SpecialistRegistry` 放在 `src/agent/workspace/registry.py`，由 `src/agent/workspace/bootstrap.py` 显式装配。各业务包的 `specialist.py` 导出定义或构造函数，声明稳定的专员 ID、适用场景、领域输入模型、执行入口、工具工厂、批准公开的 Skill 和展示资料。沿用已写入历史的 ID，例如 `seo`、`healthcare`、`exam_prep` 和 `shopping_procurement`。启动时校验重复 ID、与系统节点的冲突及接力目标是否已登记。给注册、分派和主要执行入口补简短 docstring。

注册表持有定义和依赖工厂。每次运行由自己的执行器持有消息、工具和状态。主 Agent 理解最新输入，选择已登记的专员或处理澄清，专员验证业务条件并执行本次授权的工具。能力目录使用同一专员定义和工具来源，在当前会话上下文中投影真实可用的工具及绑定的公开 Skill。原始指令只展示批准公开的静态部分。现有 `ToolRegistry` 继续负责单个专员的工具查找和校验。[工具注册](../src/agent/tools/registry.py) · [运行执行器](../src/agent/runtime/executor.py) · [能力详情组件](../frontend/src/components/SpecialistDirectory.tsx)

## 一期 · SEO 试迁

从现有 `run_seo_specialist()` 接入，整理 SEO 的工作台执行入口、目标和工具依赖以及能力目录投影。让工作台从登记定义创建 SEO 节点，`GET /api/v1/chat/tools` 使用同一条登记，其余专员用现有入口过渡。核对 `_seo_registry()` 同时被工作台、独立站点对话和恢复路径调用的情况。按当前产品边界验收 SEO 的公开网页读取、建议、工作台预览和只读复检，外部页面由站点方修改及发布。地址、电话、作者、日期等与本次任务无关的缺项直接省略。[SEO 执行入口](../src/seo_agent/specialist.py) · [工作台 API](../src/api/routes/workspace/conversations.py)

保留应用 Run、专员执行 ID、补充请求 ID 和工具回执的恢复语义。用当前快照里的 `route`、`active_agent`、`public_rule` 及现有检查点恢复任务。主 Agent 澄清收到回答时可依据最新输入校正分派。`course_review` 仍由工作台编排，医药先返回核实过的公开考纲条款、适用范围和出处，保存 `public_rule_verified` 检查点，再由 SEO 读取并核对目标课程页。只移交核对所需的公开证据与页面范围。复用考纲与页面取证的原工具调用 ID 和回执。[公开依据核验](../src/healthcare_agent/exams.py) · [当前接力和检查点](../src/agent/workspace/team.py) · [持久化恢复](../src/agent/harness/store.py)

## 二期 · 迁移其余专员并收拢调用方

让医药、学习陪练、报考备考、随访与复诊准备、商旅和采购导出各自定义。学习和报考备考独立登记，共用教育模块的基础实现。复诊、商旅和采购接入既有业务工作流。由注册定义生成分派候选、提示片段和带类型的输入约束，交给现有 PromptOps 管理路由提示及输出约定的版本。教师资格考期、商旅条件、采购条件等业务字段的校验和合并由领域模块负责。对模型返回的专员 ID 先查注册表，再执行持久化和派单。[教育 Skill](../src/education_agent/skills.py) · [复诊工作流](../src/followup_agent/workflow.py) · [商旅工作流](../src/travel_agent/workflow.py) · [采购工作流](../src/procurement_agent/workflow.py) · [现有路由提示版本定义](../src/seo_agent/automation/prompts.py)

前端从接口获取专员显示资料。对话、历史和运行中心应能接受有效的新专员 ID，并给缺少展示资料的历史记录提供通用显示。保留能力页按工具逐项展开真实 Skill 的体验。由公开定义导出旧接口的兼容快照，工具身份与 Skill 绑定按现有兼容规则匹配。每迁一组专员先复跑该组现有用例，再收掉对应的临时接线。[能力目录快照](../frontend/src/services/bundledSkillCatalog.json) · [运行中心](../frontend/src/pages/ObservabilityPage.tsx)

## 验收与演示

| 阶段 | 从普通入口操作 | 可观察结果 |
| --- | --- | --- |
| 一期 · 普通派单 | 发送公开网址并询问网页 Title，随后打开能力页 | SEO 获派，实际读取页面 HTML，显示来源。能力页能展开该工具真实绑定的 Skill 和公开指令 |
| 一期 · 补充后续跑 | 缺网址时发起页面检查，等待后刷新或重启，再补网址 | 延续原应用 Run 和补充请求。任务产出可从历史打开 |
| 一期 · 课程联查 | 用医学人文考纲核对指定课程页，在公开考纲取证后模拟中断并恢复 | 原 Run 内能看到医药与 SEO 两段执行。SEO 依据核实的条款和目标页输出，成功回执在恢复时被复用 |
| 二期 · 扩展与异常 | 注册一个走通用交互的测试专员，模拟未知路由并回看旧会话 | 测试专员可派单、展示 Skill 和回看。未知路由得到可理解的澄清或恢复原因。旧记录可读 |
| 二期 · 复诊保存 | 用合成资料整理复诊准备清单，确认保存后重试 | 历史仅有一份已经确认的资料包，保留当前保存和幂等语义 |

以现有 `test_missing_page_waits_and_resumes_the_same_run_after_restart`、`test_course_editor_gets_exact_page_and_verified_public_rule`、`test_failed_course_editor_reuses_both_receipts_after_restart` 和 `test_followup_resume_keeps_the_first_save_after_a_post_commit_failure` 做场景回归。目录复用 `test_tool_directory_lists_the_live_registries_without_running_tools` 与 `test_seo_directory_matches_the_runtime_skills_for_each_available_tool`，补充同一定义能驱动注册、分派和目录的针对性测试。最后走常规 API 和 Chromium，对话、能力、历史与运行详情都核对一遍。[场景回归](../tests/agent/test_workspace_scenarios.py) · [目录回归](../tests/agent/test_workspace_chat.py) · [工程与测试说明](engineering.md)

演示优先选课程联查。展示用户补链接后如何进入同一个 Run、医药与 SEO 分别能调用哪些工具、公开依据在哪里交接，以及中断恢复如何沿用已有证据。交付时给出改动文件、API 和浏览器实测结果、旧运行兼容情况及仍待验证的项目。
