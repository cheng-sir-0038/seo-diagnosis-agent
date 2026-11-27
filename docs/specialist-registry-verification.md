# 工作台专员注册化验收

2026 年 11 月 27 日 · seo-diagnosis-agent

正式工作台的七位专员已迁到应用注册表，沿用已有记录的专员 ID。隔离装配中，将「档案核验」加入注册表一次，即可从普通对话派单、补充编号、查看公开 Skill、重开历史结果和查看运行详情。课程联查走通了缺链接、刷新、分段取证、模拟中断、恢复和历史回看。[注册化交接](specialist-registry-handoff.md) · [应用装配](../src/agent/workspace/bootstrap.py)

## 课程联查演示

从「新建对话」发送「请用医学人文考纲核对课程页，只列出适用范围和出处。」未提供网址时，界面要求补链接，工具调用数为零。刷新后填写完整的课程页 URL，`POST /api/v1/chat/runs/{run_id}/input` 使用原补充请求 ID，响应返回同一应用 Run。[桌面等待](screenshots/specialist-registry/desktop-course-waiting.jpg) · [手机等待](screenshots/specialist-registry/mobile-course-waiting.jpg) · [对话入口](../src/api/routes/workspace/conversations.py)

医药专员先查证考纲，将公开条款、适用范围和出处保存到 `public_rule_verified` 检查点。SEO 专员随后读取补充的精确页面。结果核对的证据输入限定为 `public_rule` 和 `page`，该轮工具列表为空。取证的两条成功工具回执分别归于 `handoff_syllabus` 和 `handoff_course_page`，医药与 SEO 有各自的 `agent_run_id`。[工作台接力](../src/agent/workspace/team.py) · [SEO 执行入口](../src/seo_agent/specialist.py) · [考纲来源](../src/healthcare_agent/exams.py)

两个取证工具完成后，核对模型的第一次调用返回模拟 HTTP 503。用户点击「恢复任务」，原应用 Run 和 SEO 执行 ID 继续使用。失败态与完成态的累计事件中，两份成功回执的内容和工具调用 ID 完全一致。该课程页读取一次，模型核对共尝试两次。运行中心用同一应用 Run 查到「等待补充」「失败」「已完成」三条 Trace。失败 Trace 同时显示医药考纲取证、SEO 网页读取成功以及后续的模型报错。[桌面失败 Trace](screenshots/specialist-registry/desktop-course-trace.jpg) · [手机失败 Trace](screenshots/specialist-registry/mobile-course-trace.jpg) · [接力和恢复回归](../tests/agent/test_workspace_scenarios.py)

桌面临床课程样例与手机护理课程样例给出了不同的适用范围。两端显示「公开公告」和「课程页」链接，从「历史会话」可以重新打开本次最终结果。样例使用本地固定的公开考纲证据和合成课程页。[桌面恢复后结果](screenshots/specialist-registry/desktop-course-result.jpg) · [手机恢复后结果](screenshots/specialist-registry/mobile-course-result.jpg)

## 单点登记与普通 API

SEO 先接入登记定义。工作台、独立站点对话及其恢复、能力目录共用 SEO 工具工厂，随后六位专员迁到各自业务包。应用登记提供分派候选、领域输入约定和工具来源，通用执行入口从运行上下文构造工具并记录专员执行。适用范围和工具权限仍由业务定义与本次运行的条件决定。[注册契约](../src/agent/workspace/registry.py) · [SEO 定义](../src/seo_agent/specialist.py) · [通用执行入口](../src/agent/workspace/execution.py) · [登记说明](engineering.md#登记通用专员)

隔离环境复制了正式七位的后端，在业务包新增「档案核验」定义，并将 `ARCHIVE_SPECIALIST` 加到 `bootstrap.py` 的登记元组。浏览器复用正式装配的同一前端生产构建。发送「请核对合成档案。」后走通用执行入口，补入 `ARCH-001` 后在原 Run 完成。历史可重开结果，运行链路显示「档案核验」，能力页显示只读查询说明及批准公开的正文「按编号读取合成档案，标明档案来源。」[样例定义](../tests/agent/registered_archive_fixture.py) · [桌面 Skill](screenshots/specialist-registry/desktop-new-specialist-skill.jpg) · [手机 Skill](screenshots/specialist-registry/mobile-new-specialist-skill.jpg) · [桌面 Trace](screenshots/specialist-registry/desktop-new-specialist-trace.jpg) · [手机 Trace](screenshots/specialist-registry/mobile-new-specialist-trace.jpg)

`test_one_registration_drives_promptops_tools_and_recovery` 通过普通创建、SSE、目录、补充、重放、详情和恢复接口验证同一定义。新登记进入 PromptOps 路由候选和输出约定，实际模型适配器收到已部署的兼容版本。部署旧约定到该键时，Run 在发出模型请求前给出契约冲突原因。普通 API 和两种浏览器尺寸均验证未知专员 ID 进入澄清，派单事件数为零。API 撤下样例专员后仍可读取已完成记录，等待 Run 的有效补充先保存，继续运行返回 409，且没有新的上游调用。[API 测试](../tests/agent/test_text_model_integration.py) · [路由契约](../src/agent/workspace/routing.py) · [部署约定校验](../src/seo_agent/workflows/structured_model.py)

## 能力目录与普通入口回归

`GET /api/v1/chat/tools` 从实际工具工厂取得当前会话的工具、可用状态和绑定。展开工具会显示对应 Skill 的场景、所需输入、处理规则和交付结果，再展开可见批准公开的静态正文。商旅高铁航班使用行程核对用语，医考报名区分「报名流程核对」和「实践技能考试免考核对」。目录的正文来源限于定义中批准公开的内容及工具行明确关联的公开共用指令。复诊的三个工具均返回绑定。医药的两层展开可用键盘。原始和共用指令的正文与容器宽度在桌面均为 1041 / 1057 px，手机均为 300 / 316 px，两端均正常换行且无横向溢出。[商旅桌面](screenshots/specialist-registry/desktop-travel-approved-skill.jpg) · [商旅手机](screenshots/specialist-registry/mobile-travel-approved-skill.jpg) · [医考桌面](screenshots/specialist-registry/desktop-medical-approved-skill.jpg) · [医考手机](screenshots/specialist-registry/mobile-medical-approved-skill.jpg) · [目录响应](../src/api/routes/workspace/directory.py) · [目录组件](../frontend/src/components/SpecialistDirectory.tsx)

SEO 的独立场景从「请读取网页标题。」进入等待，刷新后补 `https://example.com/classes`，原 Run 和补充请求继续使用。应用通过实际网页元信息解析器读出合成 HTML 的标题 `Community class timetable`，在 SSE 交付，模型收到带原工具调用 ID 的结果。能力页可展开该工具实际绑定的「网页内容与标签核对」。课程核对的公开共用指令只附到读取网页的相关工具行。[桌面 SEO 工具](screenshots/specialist-registry/desktop-seo-registry-skill.jpg) · [手机 SEO 工具](screenshots/specialist-registry/mobile-seo-registry-skill.jpg) · [目录契约回归](../tests/agent/test_workspace_chat.py)

两种尺寸都从首页进入复诊资料区，刷新草稿、确认版本、重放保存、进入任务工作台并从历史打开。桌面用三份合成资料保存两条有依据的待办，手机导入一份合成门诊 CSV 即生成清单，保存零条待办。浏览器另核对同一会话的较早 Run 可返回当前等待任务，全局新建后仍可从历史打开原等待复诊。旧格式资料包、无专员显示档案的旧事件和无版本目录用存储或组件回归覆盖。旧目录按已知专员、工具和 Skill 绑定从批准快照取得正文，既有专员历史保持原 ID。[复诊与历史证据](workbench-audit.md) · [旧资料包组件回归](../frontend/src/__tests__/FollowupPanel.test.tsx) · [旧目录回归](../frontend/src/__tests__/ProductApp.test.tsx) · [专员事件状态](../frontend/src/state/agentState.ts)

数据源管理与运行中心也在两种尺寸核对。保存模型别名后界面分别显示当前生效和待重启值，Key 仅有必要的配置状态。替身回执的实际模型 `openai-actual-snapshot` 出现在成功 Trace 中。第一阶段单独使用无敏感内容验证过真实 OpenAI，结果见工作台审查。[模型验收](workbench-audit.md#复诊和模型阶段的回归与-chromium) · [配置页面](../frontend/src/pages/DataSourcesPage.tsx) · [运行中心](../frontend/src/pages/ObservabilityPage.tsx)

## 回归结果与 Git

全量后端 775 项通过，使用隔离 PostgreSQL。前端 16 个文件、127 项通过，`npm run lint` 和 `npm run build` 通过。Vite 仍报告部分产物超过 500 kB 的体积提示。定向复跑可将 `TEST_DATABASE_URL` 指向专用测试库，运行 `uv run pytest tests/agent/test_text_model_integration.py -k one_registration`。[测试库说明](engineering.md)

Chromium 159.0.8154.6 使用桌面 1440 × 960 和手机模拟 390 × 844。正式装配每种尺寸覆盖课程、能力、SEO、复诊、旧 Run 返回和模型配置，共 12 项。只在隔离装配出现的档案专员场景另外两项通过。两组均连接实际本地 FastAPI、SSE 与 PostgreSQL，使用合成网页、资料和模型上游。持久化恢复与旧格式由上述 API 和组件用例覆盖，真实用户数据库的原位升级未纳入这次验收。

工作台审查仍记录九项待处理发现。优先项是 PubMed 请求的出站范围、等待时的改题、复合附件的取证范围和首次创建回执丢失后的去重。复诊已有重放及版本冲突回归，并行双确认与并发导入适合另行验证。[待处理发现](workbench-audit.md#待处理发现按用户影响排序)

交付核对时本地 `main`、本地跟踪引用 `origin/main` 和远端 `refs/heads/main` 同为 `2af90efe3975a8bd695850d317d495f05765d5ed`。保留接手时的工作树改动，本轮提交与推送均为零。
