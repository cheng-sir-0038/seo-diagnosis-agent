# 对话截图

本组截图于 2026-11-11 使用用户提供的测试域名 `test0922.guxiguxi.com` 拍摄，来自真实只读运行及其界面回放。用户用自然语言提出业务问题，不需要输入工具名或 JSON 参数。

页面内容来自测试页；页面中提及的机构身份尚未确认。搜索查询采用站点配置的美国市场，不代表中国市场的排名。DataForSEO 对该子域名及部分主题词未返回数据，截图保留空结果和未知指标，不将其解释为零流量或全网没有排名。页面建议均未发布，内部运行标识已遮挡。

## 7 个只读工作流

| 对话场景 | 截图 | 工作流代码 |
| --- | --- | --- |
| 首页体检 | [页面证据与检查范围](02-page-audit.png) | [site_audit.py](../../src/seo_agent/workflows/site_audit.py) |
| 搜索现状 | [区分无数据与零流量](03-search-diagnosis.png) | [search_diagnosis.py](../../src/seo_agent/workflows/search_diagnosis.py) |
| 关键词机会 | [候选词与未知指标](04-keyword-opportunities.png) | [keyword_opportunity.py](../../src/seo_agent/workflows/keyword_opportunity.py) |
| 已有排名页优化 | [样本中没有匹配页](05-ranking-pages.png) | [ranking_page_optimization.py](../../src/seo_agent/workflows/ranking_page_optimization.py) |
| 综合诊断 | [汇总证据与覆盖范围](06-site-diagnosis.png) | [site_diagnosis.py](../../src/seo_agent/workflows/site_diagnosis.py) |
| 页面信息建议 | [TDK 与 OG](07-page-suggestions.png)、[JSON-LD 与待确认项](15-structured-data.png) | [page_suggestions.py](../../src/seo_agent/workflows/page_suggestions.py) |
| 页面变更复检 | [保存基线](09-page-review-baseline.png)、[按目标复检](10-page-change-review.png) | [page_change_review.py](../../src/seo_agent/workflows/page_change_review.py) |

<details>
<summary>首页体检：先看页面事实，性能未配置时保留缺项</summary>

读取标题、描述、H1、Canonical 等源 HTML 信息。此次没有配置 PageSpeed，性能不被判为通过；单页结果不代表整站审计或 Google 收录检查。

![首页 SEO 体检](02-page-audit.png)

</details>

<details>
<summary>搜索现状：没有返回指标时，明确说明未知</summary>

本次美国市场查询未返回该子域名的排名条目或站点指标。列表为空、指标不可用，不生成排名清单或零流量结论。

![自然搜索现状与数据限制](03-search-diagnosis.png)

</details>

<details>
<summary>关键词机会：联想词和候选词不等于已批准的业务机会</summary>

围绕“白酒选型”查询联想词、指标和主题词 SERP。图中候选词缺少可用的搜索量和难度，保留为未知；只读研究不会入池、审批或创建执行任务。

![关键词候选与未知指标](04-keyword-opportunities.png)

</details>

<details>
<summary>已有排名页优化：没有匹配样本时，不编造待优化页</summary>

请求调查美国市场第 4–20 名、最多两个页面。本次没有返回匹配排名页，因此没有生成已核实的页面优化问题。

![已有排名页优化的空样本结果](05-ranking-pages.png)

</details>

<details>
<summary>综合诊断：汇总已查证据，同时说明未完成的检查</summary>

组合首页体检、搜索现状、指定主题的关键词研究和自然搜索竞品查询。各步骤按实际证据汇总；数据缺失不等于检查通过，也不包含尚未实现的整站爬取或 Google 收录审核。

![综合诊断与检查覆盖范围](06-site-diagnosis.png)

</details>

<details>
<summary>页面信息建议：按当前页面生成 TDK、OG 和 JSON-LD 草稿</summary>

使用页面事实和已确认信息拟定标题、描述、主题词、分享信息及结构化数据。主体身份等缺项仍列为待确认，已有 JSON-LD 需人工确认合并；截图不代表通过 Google 富结果测试。

![TDK 与 OG 建议](07-page-suggestions.png)

![JSON-LD 草稿与来源、待确认项](15-structured-data.png)

</details>

<details>
<summary>页面变更复检：保存基线，再逐项核对明确目标</summary>

先保存页面证据，再重新读取页面并对照给定的 Title、H1 和 Canonical。此次字段与目标一致；过程没有改写网站，也不能证明排名提升。

![页面基线已保存](09-page-review-baseline.png)

![页面字段与给定目标逐项比较](10-page-change-review.png)

</details>

## 对话与人工补充

<details>
<summary>对话入口：从业务问题开始</summary>

入口提供页面体检、关键词机会和页面建议等自然语言示例，点击后填入输入框，由用户发送。

![自然语言对话入口](01-chat-overview.png)

</details>

<details>
<summary>Human-in-the-loop：保存进度，等补充后继续同一次运行</summary>

本次页面建议询问主体身份和分享图片，运行进入等待状态。截图中的“先用现有信息生成”由人工显式选择，继续同一个 Run；未确认内容保留为待补充。这一步不代表机会审批。

![等待补充信息与继续选项](08-human-input.png)

</details>

<details>
<summary>执行详情：查看实际工具、状态、耗时和运行事件</summary>

可查看本次对话的工具调用及补充前后的运行事件。模型输入输出与工作流内部子步骤由[观测模块](../../src/observability/)记录，可按配置上传 Langfuse；这张图只展示前端已有的执行详情。

![实际工具调用与运行事件](11-execution-details.png)

</details>

<details>
<summary>手机端：对话、建议卡片和输入框随屏幕排列</summary>

![手机端页面信息建议](14-mobile-chat.png)

</details>

## 其他只读查询

以下是[对话工具注册](../../src/seo_agent/chat/tools.py)中的基础查询，未封装成额外业务工作流。

<details>
<summary>竞品与外链：保留实际返回范围</summary>

本次外链与自然搜索竞品查询未返回可展示条目，界面说明查询范围与数据缺口，不虚构竞品或外链。

![竞品与外链查询](12-backlink-evidence.png)

</details>

<details>
<summary>新闻、图片与视频：为内容研究提供搜索线索</summary>

围绕同一主题查看不同类型的搜索结果，保留标题、链接或缩略图及来源。媒体搜索结果是研究线索，不作为本站的业务事实。

![新闻与内容搜索结果](13-content-search.png)

![图片搜索结果](16-image-search.png)

![视频搜索结果](17-video-search.png)

</details>
