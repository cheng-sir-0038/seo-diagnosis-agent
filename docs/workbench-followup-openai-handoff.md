# AI Agent 工作台开发交接

接手 `seo-diagnosis-agent`，用多项产品与前端 Skill 全面审查工作台，完成复诊换场景、清理复诊底部入口和 OpenAI 模型适配。以普通菜单、实际 API 和 Chromium 验收最终结果。

## 接手基线

交接时本地 `main` 的提交是 `2af90ef`，包含上一轮能力页、运行中心和专员工作流改动。已用 Git 核对远端 `main` 仍是 `de72db6`。当时推送因缺少 GitHub 写入认证失败。接手先核对 `git status --short --branch`、`git log -1 --oneline` 和 `git diff`，从当前本地代码继续，保留工作树已有改动。本文件是本轮新增的交接材料。[仓库指引](../AGENTS.md) · [本地启动](../README.md)

当前能力页已能按工具展开真实 Skill。原始指令与共用指令使用整行展开，正文在桌面用足可用宽度，在窄屏自然换行。审查时保留这项验收。SEO 的操作范围是读取网页、整理建议、生成工作台预览和只读复检。页面修改和发布由站点方负责。公司地址、电话、作者、日期等与任务无关的缺项直接省略。

## 1. 采用多个 Skill 全面审查

调用 `anthropic-skills:ai-agent-product-design` 检查路由、工具权限、追问及补充后续跑、交付和失败恢复。用 `anthropic-skills:product-design-and-ux` 检查任务路径和下一步动作，用 `anthropic-skills:frontend-design` 检查布局、展开、键盘和窄屏体验，用 `anthropic-skills:domain-modeling` 核对资料、来源、版本、待办以及模型和数据源状态的语义。依据实际代码、接口和操作路径形成结论。

从新建对话和七位专员的实际任务入手，检查附件与数据源缺失、补充和纠正、部分成功、刷新恢复、任务工作台、历史会话、Agent 能力、运行中心和数据源管理。每项发现记录用户目标、复现步骤、实际表现、影响、代码位置与验收条件，并注明实测或源码核对。优先落地以下三项，其他发现按用户影响排列。[主 Agent 分派](../src/agent/workspace/team.py) · [对话工作台](../frontend/src/pages/ChatPage.tsx) · [能力页](../frontend/src/components/SpecialistDirectory.tsx) · [运行中心](../frontend/src/pages/ObservabilityPage.tsx)

## 2. 把复诊换成「复诊前准备清单」，去掉逐条修订

新场景用合成的门诊小结、预约单和日常记录演示。示例请求是「我下周要复诊，帮我整理要带的资料、已有的预约安排和想问医生的问题。」从首页进入后，用户可以导入资料或载入合成样例。已有一份可用资料即可生成清单草稿。先展示要带的资料、已有安排、要问医生的问题。清单条目可回查到导入的文件和解析行。资料缺口影响交付时，在具体条目旁说明可以补什么。保留确认后保存当前版本资料包的动作。

撤下截图里的「核对」操作列、每条「修订」按钮、含日期和数值等字段的修订表单，以及引导用户修订的提示。用户需要补充时使用资料导入入口。资料记录和历史来源可按需查看，旧会话与已保存资料包可正常回看。核对 `PATCH /records/{record_id}` 的调用方和历史存储兼容，再决定是否移除后端入口。检查报告原值和比较依据可在相关资料中查阅。交付以整理为主，已有记录或预约才作为事实展示，用户确认后才保存对应待办。

同时核对首页快捷任务和合成示例、工作流等待文案、Agent 能力页的复诊 Skill 说明与目录快照。新场景的验收包括无资料时能找到导入入口，只有一份资料时能继续，来源可追溯，重复提交或刷新可回到同一 Run，确认保存当前草稿后能从历史打开。浏览器上看不到逐条修订入口。[首页快捷任务](../frontend/src/components/WorkspaceHome.tsx) · [复诊资料区](../frontend/src/components/FollowupPanel.tsx) · [复诊样例](../src/followup_agent/examples/initial.csv) · [专员工作流](../src/followup_agent/workflow.py) · [资料 API](../src/api/routes/workspace/followup.py) · [资料存储](../src/followup_agent/store.py) · [能力说明](../src/api/routes/workspace/business_skill_usage.py)

## 3. 去掉复诊等待态底部的「开始新任务」

移除截图中「通过上方的复诊资料区继续本次整理」旁的「开始新任务」链接。把当前可执行的动作放在资料区的等待提示旁。空壳底栏可随重复提示一起收掉，让资料区有更多可用空间。用户另开对话走左侧「新建对话」。`startNewTask` 也被普通问答等待态使用，改动前检查调用方和现有续跑测试。

用浏览器验收桌面和手机。等待补充时用户能看到本次任务的资料动作，聊天底部没有截图中的重复链接。用户仍能从左侧新建对话并从历史回到原 Run，已切到旧 Run 时「返回当前任务」可以正确回去。[等待态和新建入口](../frontend/src/pages/ChatPage.tsx) · [续跑与新建对话回归](../frontend/src/__tests__/ChatSeoAgentIntegration.test.tsx)

## 4. 让主对话实际使用已配置的 OpenAI Key

用户已配置 OpenAI Key。当前配置已有 `OPENAI_API_KEY`，本仓库将它用于 GEO 引用采样。主工作台对话和通用 Agent 路由直接构造 `DeepSeekProvider`。部分 SEO 文案、机会与自动化流程另走 `LangChainDeepSeekFactory`。模型适配须接到实际调用路径，数据源卡应反映生效的 provider 和模型。OpenAI 的真实权限与额度通过实际请求才能核对。[设置](../src/core/config.py) · [主对话](../src/api/routes/workspace/conversations.py) · [通用 Agent 路由](../src/api/routes/agent.py) · [SEO 模型构造](../src/seo_agent/runtime.py) · [GEO 模型](../src/seo_agent/geo_a2a.py)

默认选择已配置的 OpenAI，允许显式指定 DeepSeek。仅配置 DeepSeek 的部署继续可用。复用 `OPENAI_API_KEY` 和既有配置优先级，为主对话提供可配置的模型名，GEO 引用采样使用其专用配置。先核对 `ModelProvider` 接口与已有实现，复用可共用的流式和工具调用契约。OpenAI 请求使用该模型支持的参数。检查主对话、专员工具调用和 SEO 的独立文本模型入口。将 GEO 引用、图像生成和专业数据源按各自现有能力执行。[模型接口](../src/agent/providers/base.py) · [现有流式实现](../src/agent/providers/deepseek.py) · [配置示例](../.env.example)

模型配置页显示当前生效的 provider、模型及配置或待重启状态，按现有规则支持本机配置。运行详情显示本次实际用到的模型。Key 只通过服务端配置读取，API 响应、日志、Trace、截图和提交仅出现必要的状态。验证缺配置、错误 Key、模型无权限、限流和超时的反馈，避免因为自动切换模型改变数据接收方。[配置定义](../src/core/source_settings.py) · [数据源接口](../src/api/routes/workspace/sources.py) · [数据源页面](../frontend/src/pages/DataSourcesPage.tsx)

验收时只配 OpenAI，走「新建对话」发起一条合成任务，证明服务端发出 OpenAI 请求，前端接到流式结果，工具调用和工具返回可继续。复测只有 DeepSeek、两者同时配置时的显式选择、配置缺失及错误。回归测试使用上游响应替身。若当前环境的真实 Key 和网络可用，以无敏感资料的最小请求做一次 OpenAI 实测，记录 provider、模型和通过情况。

## 交付与验收

交付一份按影响排序的审查发现、三项改动及文件入口、定向前后端测试结果，以及 Chromium 中复诊流程和模型配置的桌面、手机验证。普通菜单进入复诊并完成一次「载入或导入资料 → 整理草稿 → 确认保存 → 历史回看」。核对复诊等待态无需底部新任务链接。OpenAI 验收分别标明真实请求与模拟结果。关键 Python 入口补充简短 docstring。说明本地提交和远端状态，以当前 Git 核对结果为准。[复诊组件回归](../frontend/src/__tests__/FollowupPanel.test.tsx) · [复诊工作流回归](../tests/agent/test_workspace_scenarios.py) · [数据源回归](../tests/agent/test_workspace_sources.py) · [工程与测试说明](engineering.md)
