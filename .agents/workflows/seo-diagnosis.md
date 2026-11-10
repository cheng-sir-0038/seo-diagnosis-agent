# SEO 诊断工作流

本文件帮助开发者定位现有链路；实际执行由 API 和 Agent 代码完成。

1. 前端对话入口是 [`frontend/src/pages/ChatPage.tsx`](../../frontend/src/pages/ChatPage.tsx)，后端入口是 [`src/api/routes/seo_agent_chat.py`](../../src/api/routes/seo_agent_chat.py)。
2. [`src/seo_agent/chat/runtime.py`](../../src/seo_agent/chat/runtime.py) 组织站点上下文，[`src/seo_agent/chat/tools.py`](../../src/seo_agent/chat/tools.py) 注册工具，[`src/agent/loop.py`](../../src/agent/loop.py) 负责模型与工具循环。
3. 工具经 [`DataFacade.query_live()`](../../src/facade/data_facade.py) 查询真实数据；pseudo 数据不进入正式 SEO Chat 诊断。数据不可用时应保留来源和限制，不能编造结论。
   用户要求单页面基础体检时，`seo_site_audit` 运行 [`src/seo_agent/workflows/site_audit.py`](../../src/seo_agent/workflows/site_audit.py)：先检查页面元信息，仅在用户要求时检查性能，并分别保留成功结果和失败状态。
4. [`src/agent/harness/`](../../src/agent/harness/) 保存运行事件与 Checkpoint；SSE 用于向前端展示过程。[`src/agent/evals/`](../../src/agent/evals/) 用于检查工具轨迹。
5. 长期业务中的审批由 [`src/seo_agent/approvals/graph.py`](../../src/seo_agent/approvals/graph.py) 等待用户决定后恢复。它不是每次对话的默认步骤，也不代表完整业务闭环已经验收。

修改链路时，同时检查工具选择、错误与恢复、真实数据门禁，以及相关调用方和测试。搜索引擎是否抓取或收录，需要对应证据，不能只凭页面缺少 `noindex` 下结论。
