# SEO Diagnosis Agent

一个网站增长诊断 Agent MVP：用户用自然语言提出 SEO 问题，Agent 选择需要的只读工具，结合真实数据给出结论，并保存调查过程，方便继续查看或恢复。

项目同时包含 Agent 和 SEO 数据适配层。前者负责理解问题、选择工具和组织回答；后者负责查询、统一不同来源的数据及标明来源。技术栈为 Python、FastAPI、React、LangGraph、SQLite、SSE 和 pytest。

## 已实现的部分

- **Agent Loop**：`src/agent/loop.py` 让模型根据问题和已有结果决定下一步工具调用；限制迭代次数、去重已成功的调用，并在达到预算时收束回答。
- **运行与恢复**：`src/agent/harness/` 保存 Task、Thread、Run、事件、Checkpoint 和 Survival Summary；SEO Chat 支持服务重启后恢复、取消和 SSE 断线重连。
- **轨迹评测**：`src/agent/evals/` 检查工具调用与预期轨迹，例如页面 TDK 问题不能用 SERP 冒充页面源码，模拟数据不能作为正式诊断工具。样例见 [`tests/agent_evals/`](tests/agent_evals/)。
- **审批工作流**：`src/seo_agent/approvals/graph.py` 使用 LangGraph 中断等待审批，并在收到决定后恢复；机会、任务、交付、发布和监控也有业务代码和接口，完整真实业务链路仍需验收。
- **工作台和接口**：`frontend/` 提供 React 对话与结构化结果展示；FastAPI 提供 SEO Chat、SSE、审批和兼容的 `POST /api/v1/query` 查询接口。

## 一次诊断如何运行

```text
React 工作台 / API
        ↓
SEO Chat：站点上下文 + Agent Loop
        ↓  选择只读工具，必要时继续查询
DataFacade / Provider Router
        ↓
页面 HTML、Serper、DataForSEO、PageSpeed 等数据来源
        ↓
SSE 返回过程和结论；SQLite 保存事件与检查点
```

需要人工决定的长期业务流程另由 LangGraph 审批图处理。当前 Chat 工具只读，不会自动修改网站或发布内容。

| 数据来源 | 当前状态 |
| --- | --- |
| 页面源 HTML | 可直接读取；不执行页面 JavaScript |
| Serper | 配置 Key 后提供 Google 搜索结果及相关搜索能力 |
| DataForSEO | 配置凭证后提供关键词、SERP、外链和竞品等数据；未配置时兼容查询可进入 pseudo 模式 |
| PageSpeed | 配置 Key 后提供页面性能诊断 |
| Ahrefs | 当前为 pseudo 模式，不进入正式 SEO Chat 诊断 |

正式 SEO Chat 只使用 live 工具；pseudo 结果适用于适配层和测试，不应当作真实数据。Provider 的实际可用性取决于凭证、权限和第三方服务状态。

## 本地启动

需要 Python 3.11+、Node.js 和 npm。后端从仓库根目录运行：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn src.server:app --reload
```

前端另开一个终端：

```bash
cd frontend
npm ci
npm run dev
```

访问 `http://localhost:5173`；API 文档位于 `http://localhost:8000/docs`。SEO Chat 需要在不会提交的 `.env.local` 中配置 `DEEPSEEK_API_KEY`；按需配置 `SERPER_API_KEY`、`DATAFORSEO_LOGIN`、`DATAFORSEO_API_KEY` 和 `PAGESPEED_API_KEY`。完整配置项见 [`.env.example`](.env.example)，其中示例值需要替换或留空。

本地测试可运行：

```bash
python -m pytest tests/agent tests/agent_evals -q
```

项目开发与诊断链路的 Agent 协作指引见 [`.agents/workflows/`](.agents/workflows/)。

## 目前的边界

- 这是单机 MVP，SQLite 不适合多实例生产部署；真实站点的长期成功率、费用和限流仍需持续验证。
- 审批、任务、发布和监控已有基础实现，但不等于整条业务闭环已经完成真实端到端验收。
- [seo-audit-plugin](https://github.com/cheng-sir-0038/seo-audit-plugin) 是独立的只读 SEO 插件；当前仓库尚未接入该插件的 MCP，两个项目不能描述为已联通。

本项目采用 [MIT 许可证](LICENSE)。
