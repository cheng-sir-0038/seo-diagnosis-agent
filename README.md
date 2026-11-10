# SEO Diagnosis Agent

一个网站 SEO 诊断 Agent MVP。用户用自然语言提问，Agent 选择只读工具查询数据、整理回答，并记录运行过程。项目使用 Python、FastAPI、React 和 SQLite。

## SEO Agent 工作流

业务工作流目录是 [`src/seo_agent/workflows/`](src/seo_agent/workflows/)，目前有一个单页面体检工作流 [`site_audit.py`](src/seo_agent/workflows/site_audit.py)，以 `seo_site_audit` 工具提供：

```text
seo_site_audit
  ├─ seo_page_metadata：读取页面源 HTML 和元信息
  └─ seo_page_performance：用户要求性能检查时调用 PageSpeed
```

工具在 [`src/seo_agent/chat/tools.py`](src/seo_agent/chat/tools.py) 注册，由 Agent 根据问题决定是否调用。性能检查不可用或超时时，会保留页面结果并标记为部分完成。运行事件和检查点保存在 SQLite，支持恢复；运行过程可以在工作台查看，也可以通过 Langfuse 查看模型输入、工具调用及实际执行的子步骤。

数据来源包括页面 HTML、Serper、DataForSEO 和 PageSpeed；后面三者需要配置对应凭据。页面读取不执行 JavaScript。正式 SEO Chat 只使用 live 工具，Ahrefs 当前仅为 pseudo 模式，不进入正式诊断。工具只读，不会自动修改网站或发布内容。

## 本地启动

需要 Python 3.11+、Node.js 20.19+ 或 22.12+，以及 npm。在本地 `.env.local` 配置 `DEEPSEEK_API_KEY`，其他数据源按需配置；配置项见 [`.env.example`](.env.example)。不要将密钥提交到 Git。

macOS / Linux 在仓库根目录运行：

```bash
sh start.sh
```

访问 `http://localhost:5173`，API 文档位于 `http://localhost:8000/docs`。脚本会复用已有虚拟环境和前端依赖；前端依赖变化时，可运行 `npm ci --prefix frontend`。

API 没有登录或鉴权，默认仅供本机使用，不要直接暴露到局域网或公网。

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

未配置可选数据源或 Ahrefs 仍为 pseudo 模式时，`/health` 的总体状态可能为 `degraded`，可查看各数据源状态。

### 可选：Langfuse Cloud

在 `.env.local` 中配置 `LANGFUSE_HOST`、`LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_SECRET_KEY` 并重启后端。Host 需对应项目所在区域；未配置时仍使用本地 SQLite 记录。

云端记录每轮模型输入（包括用户问题和系统提示词）、可见回答、工具选择及输入输出、执行或复用、结束原因、状态、耗时和模型返回的 Token 用量。`seo_site_audit` 还会展示实际执行的子工具。模型内部推理不在记录中。

记录可能包含站点和业务数据并发送到云端；常见凭据会脱敏，但不能保证识别所有敏感内容，超长内容会被截断。上传在后台进行，网络故障或程序过早关闭可能导致云端缺少记录，启用前的旧记录不会自动回填。

## 目前的边界

- 页面体检只检查当前站点的一个公网页面，不检查 robots.txt、sitemap 或 Google 收录。PageSpeed 可能超过默认超时，可通过 `AGENT_TOOL_TIMEOUT` 调整。
- 审批、任务、发布和监控已有基础实现，但完整业务链路尚未完成真实端到端验收。[LangGraph 审批图](src/seo_agent/approvals/graph.py)已独立测试，尚未接入审批接口。
- 创建任务时，所有关联机会必须属于当前站点，且最新的“将机会加入生产”审批已通过。中低价值机会不会自动发起审批，可通过审批接口手动创建，工作台尚无申请入口。HTML 发布要求提交内容与已审核内容一致。
- 这是单机 MVP，SQLite 不适合多实例生产部署。[seo-audit-plugin](https://github.com/cheng-sir-0038/seo-audit-plugin) 是独立项目，本仓库尚未接入其 MCP，也不支持 GSC 或 GA4 数据源。

## 测试

```bash
.venv/bin/python -m pytest tests/agent tests/agent_evals tests/seo_agent/test_site_audit_workflow.py -q
```

本项目采用 [MIT 许可证](LICENSE)。
