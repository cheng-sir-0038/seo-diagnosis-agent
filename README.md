# SEO Diagnosis Agent

在工作台提问后，LangGraph 主 Agent 分派给 SEO/GEO、医药、学习陪练、报考备考、随访与复诊准备、商旅行程或购物与采购专员。常规请求用 DeepSeek 判断业务，明确的复诊资料请求直接进入资料整理流程。对话显示专员进度和 Skill，展开「执行详情」可核对工具回执。

![多 Agent 工作台首页，含考研、医药和网站业务入口](docs/screenshots/01-chat-overview.png)

## 双专员实跑

用户请 Agent 用医学人文修订通知核对湖北考区的医师资格考试公告。医药专员加载「医考考纲核对」Skill，取出适用范围与公告原文。SEO/GEO 专员接手读取用户指定的网页，对照两份材料后给出结论。截图保留了原始提问、交接顺序和两份依据。[工作流代码](src/agent/workspace/supervisor.py) · [公开报名页](https://www.hbwsrc.cn/news/show-1830.html)

![完整提问、医药与 SEO 专员的交接顺序、核对结论和来源](docs/screenshots/24-healthcare-seo-handoff.png)

## 可以直接试的业务

| 可以这样问 | 会看到什么 |
| --- | --- |
| “帮我看看 https://breadtopia.com/how-to-make-a-sourdough-starter/ 的标题和正文是否匹配。” | SEO 专员读取指定页面，追问分享文案时沿用同一地址。 |
| “二甲双胍在当前国家医保药品目录里是甲类还是乙类？” | 读取国家医保局发布的目录 PDF，按通用名核对西药条目，逐剂型展示分类和页码。 |
| “找 2024 至 2025 年关于青少年睡眠时长的 3 篇 PubMed 文献。” | 调用 NCBI E-utilities，返回实际匹配的题录、期刊、日期和 PMID。 |
| “按 2026 年湖北医师资格公告，我 2025 年技能通过、综合没过，还要重考技能吗？” | 医药专员核对考区、年份和考点条件，缺少条件会暂停，等用户补充后继续。 |
| “对比 2026 年武汉科技大学 105101 和湖北民族大学 105100 的初试科目。” | 医药专员按各校专业代码做 PostgreSQL 全文检索，用命中段落和官方出处生成可核对的回答。 |
| “用医学人文修订通知核对湖北医师资格考试报名页的适用类别。” | 医药专员查公告，SEO 专员读取用户给出的网页，核对两份原文。[湖北考区报名页](https://www.hbwsrc.cn/news/show-1830.html) |
| “出 3 道一元一次方程题，一次出一道。” | 学习陪练从 TAL-SCQ5K 选题。回复选项字母后看到判分与原解析，中途可回来继续。 |
| “9 岁学分数，有什么官方练习？” | 按年龄和主题推荐 Oxford Owl 或 Cambridge NRICH 的原站练习，做完可在本对话登记。 |
| “2026 年下半年湖北初中数学教资笔试怎么准备？” | 报考备考核对公告和科目，给出报名清单、备考顺序与 304 大纲链接。 |
| “用合成资料演示一次复诊准备，帮我对照两次报告。” | 随访与复诊准备专员先追问缺的报告，补齐后按记录条件对照，展示来源并等你确认保存资料包和待办。 |
| “我去杭州出差。” | 先给目的地近期天气和出行建议。补上日期后按实际行程更新天气，其他条件齐全后可继续比较交通、住宿和接驳。 |
| “要买 60 个 200ml 纸杯，帮我比较这两份报价。” | 对齐规格和计量单位，按起订量、整包和阶梯价算实际采购数量及已知费用。 |

医考工具使用带年份和出处的官方公告摘录，网页核对直接读取指定的公开页面。[专员编排](src/agent/workspace/supervisor.py) · [医药 Skill](src/healthcare_agent/skills.py)

考研检索目前收录上述两校 2025、2026 年目录中的部分临床医学专硕条目。回执记录本轮 Run、问题、筛选条件、返回上限及实际段落。资料缺失时说明缺口，缺少院校、年份或专业时会追问。固定用例的验收通过率按全部用例计算，Hit@K 按有标注证据的可回答题计算。[收录的资料与来源](src/healthcare_agent/postgraduate_sources.py) · [检索与引用校验](src/healthcare_agent/postgraduate.py) · [评测口径](src/healthcare_agent/postgraduate_eval.py)

**Breadtopia GEO 实跑**　Agent 从 Google 搜索样本中选出一题，核验 DeepSeek 的 5 条外站引用，并找到 3 段适合前置的页面原文。 [完整对话](docs/screenshots/22-geo-breadtopia-conversation.png)

- **跨进程交接**　主 Agent 通过 A2A 把问题发给独立的引用 Agent。两边用同一个消息 ID 对上交接，Task 回执对应本次引用结果。 [Langfuse A2A 交接](docs/screenshots/23-geo-langfuse-a2a.png) · [交接代码](src/seo_agent/geo_a2a.py)
- **改稿证据校验**　实跑时曾把原文已有的内容写成缺失。修复后，卡片和总结共用核对过的原文，把已有内容写成前置建议。 [修复提交](https://github.com/cheng-sir-0038/seo-diagnosis-agent/commit/832f772b638943bf0664965d592325ad88fa19a2) · [回归用例](tests/seo_agent/test_geo.py)
- **人工补充与恢复**　页面信息不足时先保存取到的证据并暂停，用户补充或选择继续后接回同一个 Run。 [等待补充截图](docs/screenshots/08-human-input.png) · [恢复代码](src/agent/harness/)

## 页面建议与人工确认

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

[首页体检](docs/screenshots/19-homepage-priority.png) · [关键词机会](docs/screenshots/04-keyword-opportunities.png) · [已有排名页优化](docs/screenshots/05-ranking-pages.png) · [Semrush 竞品分析演示](docs/screenshots/20-semrush-competition.png) · [Breadtopia GEO 实跑](#geo-多-agent) · [完整对话截图](docs/screenshots/README.md) · [本地启动](#本地启动)

## Agent 如何运行

- 主 Agent 用 LangGraph 决定交给谁处理。医药专员从 `SkillRegistry` 加载本轮业务指令，并只开放该 Skill 对应的工具。工具取得有效回执后，程序直接按证据给出答案。网页联查只传已核实的公开条款。恢复时读取已经选定的业务和目标页。[业务路由](src/agent/workspace/routing.py) · [工作流](src/agent/workspace/supervisor.py)
- 通用对话使用 POST `/api/v1/chat/runs`，提交 `messages` 和可选的 `conversation_id`。主 Agent 先分派业务，专员再按已有证据选择工具。代码限制模型轮次、工具调用总数和超时，复用本次运行中重复的成功查询。[业务会话 API](src/api/routes/workspace/conversations.py) · [Agent 执行器](src/agent/runtime/executor.py)
- 工作流安排取证步骤、校验实际来源和结构化产物。需要人工补充时，运行检查点保存已查到的证据。[工作流代码](src/seo_agent/workflows/) · [检查点与恢复](src/agent/harness/)
- 查询结果和来源紧跟对话结论。「执行详情」默认折叠，展开可查看调用状态、耗时和已取得的参数。配置 Langfuse 后可查看模型和工作流的完整调用树。[观测代码](src/observability/)

```mermaid
flowchart TB
    UI["React 对话"] --> API["FastAPI"]
    API --> MAIN["LangGraph 主 Agent / DeepSeek"]
    MAIN --> MED["医药专员 / 按任务加载 Skill"]
    MAIN --> SEO["SEO/GEO 专员"]
    MAIN --> LEARN["学习陪练 / 方程练习与资源"]
    MAIN --> EXAM["报考备考 / 教资笔试"]
    MAIN --> VISIT["随访与复诊准备 / 本次提供的资料"]
    MAIN --> TRAVEL["商旅行程 / 查候选、接驳和天气"]
    MAIN --> PROCUREMENT["购物与采购 / 报价、清单和到货"]
    MED -->|公开考纲依据| SEO
    SEO --> EXEC["Agent 执行器"]
    MED --> EXEC
    LEARN --> EXEC
    EXAM --> EXEC
    VISIT --> EXEC
    TRAVEL --> EXEC
    PROCUREMENT --> EXEC
    EXEC --> HEALTH["医考公告 / 考研检索 / 医保目录 / PubMed"]
    HEALTH --> RUNDB
    EXEC --> FOLLOWUP["资料读取回执 / 对照 / 确认保存"]
    FOLLOWUP --> RUNDB
    EXEC --> TRIP["飞猪 / 百度地图 / Open-Meteo / 行程草稿"]
    EXEC --> WF["业务工作流：页面 / 搜索 / 关键词 / 草稿"]
    EXEC --> TOOL["只读 SEO 工具"]
    WF --> TOOL
    WF --> CF["Cloudflare Workers AI（分享图）"]
    TOOL --> DATA["Data Facade + Provider Router"]
    DATA --> EXT["页面 HTML / Serper / DataForSEO / Semrush / PageSpeed"]
    EXEC <--> HARNESS["Harness：事件、检查点、恢复"]
    MED --> HARNESS
    HARNESS --> RUNDB[("PostgreSQL")]
    EXEC --> OBS["Observability：模型、工具与子步骤"]
    OBS --> RUNDB
    OBS -.->|可选| LF["Langfuse Cloud"]
    API --> BIZ["机会 / 审批 / 任务 / 发布 / 监控接口"]
    BIZ --> RUNDB
```

对话中的站点取证只读，建议和生成图保存在本应用。创建执行任务、审批和发布走各自的业务接口。

### 学习陪练和报考备考

- 学习陪练收录 TAL-SCQ5K 的 50 道「一元一次方程」及应用题，一组最多 5 道。程序按题库的选项判分，保留原解析和出处。每答一题保存回执，刷新页面或重启服务后还能接着答。回复「跳过」或「结束练习」分别计入跳过和未答；完成后在同一对话说「重练错题」，可以再次练习错题。[题库来源与许可](src/education_agent/corpus/README.md) · [练习实现](src/education_agent/practice.py)
- 官方资源按年龄和主题匹配。可试「给 15 岁学生找一道方程探究题」，做完后回复「我完成了 NRICH 的方程练习」来记录。应用保存官方链接和完成状态。[资源索引](src/education_agent/resources.py)
- 报考备考覆盖湖北 2026 年上下半年的初中数学教师资格笔试。条件不全时追问，年度、地区、学段或科目超出资料范围时提示缺口。两条业务使用现有 DeepSeek 配置，题库与公开资料无需额外 API Key。[业务 Skill](src/education_agent/skills.py) · [湖北报名公告](https://www.hbea.edu.cn/html/jszg/index.html) · [中国教育考试网 304 大纲](https://ntce.neea.edu.cn/html1/report/1508/347-1.htm)

### 随访与复诊准备

从首页点「随访与复诊准备」发送合成演示。初始只有一份报告。载入合成补充报告后，在同一个 Run 里点「整理并核对资料」。资料区保留每行的来源，展示报告差值、不可直接比较的项目，以及原记录中的安排。核对后点保存，程序将当前版本和已确认的待办写入 PostgreSQL。修订资料会更新版本，旧预览需要重新核对。[Agent 工作流](src/followup_agent/workflow.py) · [验收用例](tests/agent/test_workspace_scenarios.py)

手动导入时使用匿名对象编号。首版支持 UTF-8 CSV、记录数组 JSON 和同字段模板的文字 PDF。PDF 每页首行是列名，字段用 `|` 分隔。必填字段是 `subject_id`、`kind` 和 `item`，日期使用 `YYYY-MM-DD`。样例展示了报告、已有安排、用户自述和用药资料的字段。[合成资料模板](src/followup_agent/examples/initial.csv) · [合成补充报告](src/followup_agent/examples/supplement.csv) · [FHIR R4 Observation 字段参考](https://hl7.org/fhir/R4/observation.html)

资料内容保存在当前服务的独立资料表。模型对话和执行日志只使用步骤及数量回执。报告对照仅核对原值与来源，临床解释由医生或药师负责。使用合成或已去标识的记录即可演示。

### 商旅行程

从首页点「比较出差方案」，可试一次国内单目的地往返。Agent 确认日期、会场、人数和住宿房间数后，展示飞猪的火车、航班和酒店候选。百度驾车估算和用户要求的到场缓冲分别处理。[行程工作流](src/travel_agent/workflow.py) · [方案核算](src/travel_agent/plan.py)

知道目的地就能查天气。日期未定时先给从今天起三天的近期参考，知道出发日后查当天，知道返程日后覆盖行程日期。出行建议按返回的温度、降水概率和风速给出。天气卡保留实际预报日期与来源，超出预报范围会提醒临近出发再查。[Open-Meteo 文档](https://open-meteo.com/en/docs)

候选卡保留来源、查询时间和平台入口。房型、报价单位或币种缺失时，会显示待核对项。已知费用按币种列出，平台参考价和未取得的接驳费不计入可比总价。平台缺少结果时，也可以粘贴自己查到的候选和报价，页面会标为「用户提供」。

在原对话里修改会议截止时间会沿用已有查询，改返程日期会重查返程及受影响的酒店和天气。交通接口暂时异常时，可回复「重查交通」。回复「保存方案 1」一类的确认后，所选版本随运行检查点写入 PostgreSQL。预订入口用于去平台复核，项目只保存行程草稿。

### 购物与采购

- 找商品可以说「帮我查 200ml 纸杯的参考价」。商品搜索工作流按名称和规格查最多 5 件，卡片显示来源、店铺、包装和参考价。同一来源、关键词和天猫筛选条件的查询缓存 2 小时。
- 比报价可以直接说「我要买 60 个 200ml 纸杯」并贴商家原文或带表头的报价表。给出包装、起订量、阶梯单价和已知费用后，卡片会分别显示需求量、实买量、多买多少和支出。规格不同的报价单独标出。缺运费、税费或适用优惠时显示已知小计和待核对项。
- 活动清单可从首页点「列活动采购清单」。一轮最多 5 种物品。按人数推算的用量会标为估算，有对应报价时可核对采购组合、预算和交期。询价草稿可以复制给商家。
- 到货核对可以贴脱敏的订单、收货、付款、发票和退款记录，提供共同的订单号和各自的凭证编号。结果按订单归集并显示应到、实到与差异。净支出按确认的付款与到账退款计算。

在同一个 Run 内可继续补报价或改数量。程序会按新数量重算整包和阶梯价，旧报价的运费等批次费用会标为待复核。淘宝联盟提供平台候选和券资料，预估价保留查询时间。1688 等来源可粘贴自行取得的报价，由用户核对后在平台完成购买。[采购工作流](src/procurement_agent/workflow.py) · [金额与到货核算](src/procurement_agent/calculations.py)

## 业务场景与现有入口

下面按业务问题说明已有代码能支撑什么；工具组合取决于模型判断、数据源配置和本次查询结果。

| 业务场景与输入示例 | 实际工具或服务 | 产出与当前状态 |
| --- | --- | --- |
| 页面体检：“客户从搜索点进首页，能看懂我们提供什么吗？帮我找出最值得先改的地方。” | `seo_site_audit` → `seo_page_metadata`，按需调用 `seo_page_performance` | 页面证据和优先建议。需要检查速度时可加上移动端性能数据 |
| 页面信息建议：“根据这一页的内容，帮我拟定标题、描述、分享信息和结构化数据。” | `seo_page_suggestions` → 页面事实、模型草稿、引用校验、缺失信息追问 | 给出 TDK、OG、JSON-LD 建议；用户补充后沿用对话继续生成，不修改网站 |
| 页面分享图：“按这页内容帮我生成一张分享图。” | `seo_share_image` → 读取页面、校验主体依据、调用 Cloudflare Workers AI | 配置生图服务后在对话中直接显示图片 |
| 搜索现状：“看看本站的自然流量估算、排名分布和代表关键词。” | `seo_search_diagnosis` → `seo_search_metrics` + `seo_organic_keywords` | 站点指标、有限排名样本和对应页面；只查排名词时使用关键词查询，样本为空时说明数据缺口 |
| 关键词机会：“围绕手冲咖啡，有哪些关键词值得研究？” | `seo_keyword_opportunity` → 联想词、批量关键词指标、输入主题的 SERP | 去重后的候选词和可用指标；已封装为工作流，缺失指标保持未知 |
| GEO 研究：“客户会问哪些问题？查一题在 AI 搜索里引用了谁，再给当前页一段改稿。” | `seo_geo_questions`、`seo_geo_citations`、`seo_geo_content` | 原始问题及来源、带任务回执的引用样本、可核对的改稿 |
| 机会生成与入池：显式提交一次机会发现请求 | `OpportunityDiscoveryWorkflow` → 关键词研究、冻结快照、模型候选、证据校验、既有入池服务 | 生成至多 3 个机会并沿用原有评分和审批策略；独立业务接口触发，不是只读 Chat 工具 |
| 已有排名页优化：“哪些已有排名的页面值得进一步检查？” | `seo_ranking_page_optimization` → 排名样本、少量站内 HTML、每页一个代表词 SERP | 默认调查第 4–20 名样本中的 2 个站内页面，输出实际标签、搜索证据和待核实项；不修改页面 |
| 页面变更复检：“按之前的快照，检查 Title、H1 和 Canonical 是否符合目标。” | `seo_page_change_review` → 本站基线、重新读取页面、冻结新快照、逐项比较 | 输出变更明细和验收结论；先显式建立基线，缺少目标值或完整证据时不判定通过 |
| 竞品分析：“谁在和本站竞争这些搜索词？” | `seo_site_diagnosis` 的可选竞品步骤，或 `seo_organic_competitors`、`seo_serp_overview` | 自然搜索竞品、交集指标及指定关键词的搜索结果，不代表完整关键词差距 |
| 月度自然搜索竞品：“用 Semrush 看看本站在美国的流量价值和主要搜索竞品。” | `seo_semrush_competition` → 站点概览、排名词、自然搜索竞品 | 流量价值估算、最多三条排名词的位次变化，以及最多三家竞品的共同关键词与竞争度 |
| 站点综合诊断：“检查本站页面、抓取规则和站点地图。” | `seo_site_diagnosis` → 页面体检、robots.txt、sitemap，可补查询搜索表现和关键词机会 | 页面问题证据、站点文件和已完成的检查范围 |
| 已批准机会任务规划：显式提交已批准机会与负责人 | `TaskPlanningWorkflow` → 校验最新审批、生成 PRD 内容、原子写入任务和文档记录 | 创建草稿任务、PRD 和本地 Markdown/PDF；支持幂等重试，不自动派发或发布 |
| 交付审核：提交交付后，按对应 PRD 检查 | [`review_delivery`](src/seo_agent/workflows/task_delivery.py) | 模型生成通过项、失败项和修改意见，再由服务校验并记录审核；通过后仍需走发布规则 |

只读关键词工作流产出研究候选与证据，不会写入业务机会。显式调用机会发现接口后，才会生成并校验 `OpportunityCandidate`、进入既有机会池；两条路径都不会批准机会、创建任务或发布。搜索表现来自第三方数据源，不能据此冒充 GSC/GA4 的实际访问量或完整下降归因。

## 工具与业务工作流

在工作台点「专员与工具」，可以查看五位专员及其可用工具。对话回复按实际交接顺序展示专员和操作。展开「执行详情」可核对每次调用的状态与耗时。

| 医药 Skill | 注册工具 | 免费公开资料 |
| --- | --- | --- |
| 医考报名核对、医考考纲核对 | `medical_exam_registration`、`medical_exam_syllabus` | [按考区和版次核对的公告摘录](src/healthcare_agent/exams.py) |
| 考研招生核对 | `postgraduate_admissions_search` | [院校官网目录入库，返回分校筛选与命中回执](src/healthcare_agent/postgraduate.py) |
| 医保药品目录 | `medicare_drug_catalogue` | [国家医保局 2025 年版 PDF](https://www.nhsa.gov.cn/art/2025/12/7/art_53_18971.html) · [2026 年版 PDF](https://www.nhsa.gov.cn/art/2026/11/14/art_104_23384.html) |
| 医学文献检索 | `pubmed_literature_search` | [NCBI E-utilities API](https://www.ncbi.nlm.nih.gov/home/develop/api/) |

医保工具覆盖两版常规西药目录，默认按公告生效日期选版。PubMed 可匿名查询公开题录，工具按 NCBI 公布的无 Key 速率限制发送请求。两项公开查询无需新增第三方 API Key。[NCBI 接口使用说明](https://www.ncbi.nlm.nih.gov/books/NBK25497/)

「网页与考纲联查」会把医药专员查到的考纲交给 SEO 专员。后者读取用户指定网页，对照原句核对适用类别。[跨专员工作流](src/agent/workspace/supervisor.py)

SEO / GEO 专员按当前数据源、已确认的网址和任务注册工具。[SEO 工具注册表](src/seo_agent/chat/tools.py)

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
| [`semrush_competition.py`](src/seo_agent/workflows/semrush_competition.py) | `seo_semrush_competition` | 使用 Semrush v4 查询同一市场的站点概览、自然排名与搜索竞品，保留各步证据 |
| [`keyword_opportunity.py`](src/seo_agent/workflows/keyword_opportunity.py) | `seo_keyword_opportunity` | 围绕用户主题，组合联想词、候选词指标与主题 SERP |
| [`geo.py`](src/seo_agent/workflows/geo.py) | `seo_geo_questions`、`seo_geo_citations`、`seo_geo_content` | 分别完成选题、引用核验及改稿，支持将三段串起来运行 |
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

缺少必要信息时进入 Human-in-the-loop：每次询问最多三个具体问题，将页面证据和待答问题保存到检查点，运行变为 `waiting_for_input`，停止继续调用模型。用户在同一对话中回答后，前端通过 `POST /api/v1/chat/runs/{run_id}/input` 提交符合 [`HumanInputAnswer`](src/agent/models.py) Schema 的回答，恢复同一 Run、Task 和 Thread；相同页面复用已保存的证据，调用预算不重置。回答、事件和检查点原子保存，同一请求重复提交复用回执，冲突回答拒绝；服务重启不会自动跳过等待。这个补充步骤不代表机会审批。

生成中刷新页面时，前端使用保存的 Run ID 和本轮起始事件编号，补回本轮进度并继续接收结果。同一个 Run 的各条事件连接独立读取事件。

已有有效草稿时，用户也可选择“先用现有信息生成”（`action=use_available`）：未确认内容列为待补充，不再阻塞本轮建议；事实与格式校验仍执行。没有有效草稿的必要补充不能用这个选择跳过。

页面原文和用户补充分开标记。没有依据的价格、评分、日期、作者或图片不自动补齐；不能确认结构化类型时先保留可确定的文案并追问。没有执行 JavaScript，正文最多读取 6000 字符，对话补充最多使用最近 5 条用户输入、每条 2000 字符。HTML 片段保留在结果中，JSON-LD 在卡片展示；提示词、模型调用和引用校验可在运行记录中查看。

结果始终是待人工确认的建议，不修改外部网站、Canonical、审批或发布记录。已有 JSON-LD 需人工确认合并，不能直接覆盖。运行时只做 JSON 结构与来源校验，落地前需使用 [Schema.org 验证器](https://validator.schema.org/)和适用的 [Google 富结果测试](https://search.google.com/test/rich-results)另行验证；语法通过不保证符合 Google 展示规则。站点名称不适用富结果测试，需按 [Google 站点名称规则](https://developers.google.com/search/docs/appearance/site-names)核对。[Google 不使用 Meta Keywords 参与收录和排名](https://developers.google.com/search/docs/crawling-indexing/special-tags#unsupported)，关键词仅作为主题文案建议。

### 页面分享图

在对话中说「按这页内容帮我生成一张分享图」即可，省略地址时取当前站点首页。工作流从页面的 `og:title`、Title、H1 中依次选取标题，描述取 `og:description` 或 Meta Description。标题都缺失时，同一次运行等待用户输入，恢复后复用页面证据。

DeepSeek 根据页面内容和用户要求选取主体及构图。代码校验 JSON Schema 和引用原文，缺少主体依据时请用户补充。Cloudflare Workers AI 生成 1216 × 640 的图片，图片保存在 `SHARE_IMAGE_DIRECTORY` 指定的目录，并在对话中与页面文案一起展示。绘图提示词可在运行的模型调用记录中查看。上游失败时保留已取得的页面证据。[对话截图](docs/screenshots/18-share-image-brief.png) · [工作流](src/seo_agent/workflows/share_image.py) · [Cloudflare 接入](src/seo_agent/workflows/cloudflare_images.py) · [业务测试](tests/seo_agent/test_share_image.py)

### GEO 多 Agent

> 围绕 how to make sourdough starter，给 Breadtopia 的酸种酵头教程做一次 GEO 分析。从 Google 上实际出现的问题里选一个，核对 DeepSeek 这次引用了谁，再按页面正文给出改稿建议。

本次选题保留 4 条 Google 相关问题和 1 条联想词，选中 **How do you start to make sourdough starter?**。DeepSeek 返回 5 条引用，均来自其他域名。内容编辑建议前置酵头定义、影响因素及文章涵盖的内容。[目标页面](https://breadtopia.com/how-to-make-a-sourdough-starter/)

<details>
<summary>查看 Breadtopia 实跑的完整对话和 A2A 交接</summary>

对话图重放同一次 DeepSeek 实跑的提问、证据卡片和总结。三张卡片直接展示在 3500 字符正文摘录中核对过的原文，总结逐条说明哪些已有内容适合前置。起始配比、喂养频率等细节留待核实。本次运行使用 `AGENT_CONTEXT_CHARACTER_LIMIT=80000`。

![Breadtopia GEO 的完整提问、选题、引用结果与标明证据范围的改稿建议](docs/screenshots/22-geo-breadtopia-conversation.png)

链路图上下展示本次运行的两条 Langfuse Trace。主 Agent 通过 HTTP 发送 JSON-RPC 请求，两边用同一个 `a2a_message_id` 对应交接，返回的 Task ID 对应本轮引用结果。

![Langfuse 多 Agent 链路及同一个 A2A 消息 ID 和 Task 回执](docs/screenshots/23-geo-langfuse-a2a.png)

</details>

选题和改稿各有一份模型提示词与 JSON Schema。引用核验是独立的 A2A Agent，可在同一进程运行，也可经 HTTP 跨进程调用。给出主题时用一条链完成三段。已给出具体问题时直接核验引用并审阅页面。

```mermaid
sequenceDiagram
    participant S as 主 SEO Agent
    participant Q as 选题研究员
    participant V as 引用核验 Agent
    participant E as 内容编辑
    S->>Q: 主题和目标页
    Q-->>S: 原始问题、来源、选题理由及页面证据
    S->>V: A2A Agent Card + SendMessage
    V-->>S: 目标域名匹配结论、引用链接和任务回执
    S->>E: 已选问题、页面原文与引用片段
    E-->>S: 有依据的改稿和待核实项
```

选题使用 Serper 的 Google 关联问题和联想词，每种各查一次。引用核验只采样一个问题，按模型回答中的 `url_citation` 匹配目标域名和子域。报告标明查询时间、模型和链接，分别呈现本次没有搜、搜索失败、没有返回引用，以及返回的引用未命中目标站。改稿从目标页最多 3500 字符的正文摘录选出适合前置的原文，逐条校验原文和引用 ID。卡片与总结使用同一批前置建议。外部引用用于回答引用核验结果，无法从摘录确认的细节留待核实。

默认引用来源是 DeepSeek，使用 `DEEPSEEK_API_KEY` 调官方 `deepseek-flash` 和 `deepseek_web_search`。可选填 `OPENAI_API_KEY`，并在对话中明确要求用 OpenAI API 核验，使用 Responses `web_search`。`GEO_OPENAI_MODEL` 默认 `gpt-6.2-astra`。选题、主对话和改稿沿用已有 DeepSeek 配置。每次结论限定在指定模型的 API 样本。 [DeepSeek 文档](https://api-docs.deepseek.com/guides/web_tools/) · [OpenAI 文档](https://developers.openai.com/api/docs/guides/tools-web-search)

独立启动引用 Agent 时，在主服务的 `.env.local` 设置 `GEO_A2A_URL=http://127.0.0.1:8011`，重启主服务并在同一仓库另开终端运行。

```bash
.venv/bin/python -m uvicorn src.seo_agent.geo_a2a:create_app --factory --host 127.0.0.1 --port 8011
```

引用服务在 `/.well-known/agent-card.json` 提供 Agent Card，在 `/a2a` 接受 A2A 1.0 JSON-RPC `SendMessage`，回传带 `geo-citation-report` 的 Task。演示服务将 Task 存在内存。配置 Langfuse 时可查看专员接收的证据、选题和改稿的模型生成及引用请求。跨进程用 `a2a_message_id` 关联两边的记录，Task ID 对应核验报告。 [A2A 代码](src/seo_agent/geo_a2a.py) · [引用适配器](src/seo_agent/geo_search.py) · [业务测试](tests/seo_agent/test_geo.py) · [A2A 协议](https://a2a-protocol.org/latest/specification/)

本地回归运行实际工作流和进程内 A2A，以模拟上游响应验证 DeepSeek 和 OpenAI 适配器、引用缺失及改稿证据校验，无需第三方 API Key。

```bash
.venv/bin/python -m pytest -q tests/seo_agent/test_geo.py
```

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

站点范围由扫描记录决定，不能在请求中替换 `site_id`。流程复用关键词研究，将结果冻结到PostgreSQL 证据快照库，再由 DeepSeek 生成结构化候选。代码校验市场、关键词、竞品 URL 和快照引用；搜索量、难度使用首个关键词的实际指标，缺失时保持未知。模型可以明确返回空候选列表。

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
| OpenAI（可选） | GEO 引用核验，采样 OpenAI Responses 联网回答 | `OPENAI_API_KEY`；可选 `GEO_OPENAI_MODEL` | 需要 API 项目访问权限和额度。默认 GEO 引用采样使用 DeepSeek。[GEO 配置](#geo-多-agent) |
| Serper | Google SERP、联想词、关联问题、新闻、图片、视频搜索 | `SERPER_API_KEY` | 需要 Serper API 账号、有效 Key 和相应调用额度 |
| DataForSEO | 关键词指标、自然排名、竞品、外链及 SERP | `DATAFORSEO_LOGIN`、`DATAFORSEO_API_KEY` | 分别填写 API Access 提供的 login 和 API password（API 专用密码）；还需相应端点权限及可用余额或额度 |
| Semrush（可选） | 站点月度概览、自然排名与搜索竞品 | `SEMRUSH_API_KEY` | 需 v4 Key、SEO API 的 Domain Analytics 权限及可用 API units |
| PageSpeed | 单页面 Lighthouse 实验室检查 | `PAGESPEED_API_KEY` | 本项目要求配置 PageSpeed API Key，且请求需满足对应 Google API 项目的权限、限制和配额 |
| Cloudflare Workers AI（可选） | 用 FLUX.2 klein 4B 生成页面分享图 | `CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_API_TOKEN` | 同时需配置 DeepSeek。[配置步骤](#cloudflare-workers-ai-生图) |
| Langfuse Cloud（可选） | 将模型和工具运行记录上传到项目 | `LANGFUSE_HOST`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY` | 需要 Langfuse 项目及有写入权限的项目密钥，Host 与项目区域一致；不配置时保留本地记录 |
| 飞猪 FlyAI（商旅） | 查询火车、航班及酒店候选 | `FLYAI_API_KEY`、可选 `FLYAI_EXECUTABLE` | 从飞猪控制台获取可用 Key，并在运行后端的机器或容器中安装官方 CLI。[飞猪快速开始](https://flyai.open.fliggy.com/docs/quickstart) |
| 百度地图（商旅） | 将地点解析成坐标，查询驾车距离和耗时 | `BAIDU_MAPS_AK` | 需 Web 服务 AK，并开通地理编码、地点检索和轻量级驾车路线等所需权限。[百度地图 Web API](https://lbsyun.baidu.com/docs/webapi) |
| Open-Meteo（商旅） | 查城市与逐日天气预报 | 无 Key | 当前用非商业免费端点，预报最多覆盖未来 16 天。[额度及许可](https://open-meteo.com/en/pricing) |
| 淘宝联盟（购物与采购） | 商品搜索、简版详情、指定推广券资料 | `TAOBAO_TBK_APP_KEY`、`TAOBAO_TBK_APP_SECRET`、搜索另需 `TAOBAO_TBK_ADZONE_ID` | 需应用已获得对应接口权限和额度。留空时可用自己提供的报价做比价、清单和到货核对。[搜索](https://developer.alibaba.com/docs/api.htm?apiId=64759&source=search) · [详情](https://developer.alibaba.com/docs/api.htm?apiId=64763) · [推广券](https://developer.alibaba.com/docs/api.htm?apiId=31106) |
| 本地商品库（购物与采购） | 按关键词、规格和天猫筛选查商品 | `TAOBAO_TBK_LOCAL_CATALOG=true` | 在本地环境开启。25 个品类共 1,000 条商品由项目生成，使用淘宝联盟商品搜索的 JSON 回包结构。[商品库代码](src/procurement_agent/local_catalog.py) · [接口文档](https://developer.alibaba.com/docs/api.htm?apiId=64759) |

飞猪适配已验证官方 CLI `1.0.16`。本机可运行 `npm install -g @fly-ai/flyai-cli@1.0.16`，或者将 `FLYAI_EXECUTABLE` 指向已安装的 `flyai` 完整路径。商旅仍使用已有 DeepSeek 配置。缺飞猪或百度凭据时，来源卡会展示配置状态，天气仍可单独查询。[FlyAI 源码和许可证](https://github.com/alibaba-flyai/flyai-skill)

DataForSEO 的关键词和站点指标分别使用 [Keyword Overview](https://docs.dataforseo.com/v3/dataforseo_labs-google-keyword_overview-live/) 和 [Domain Rank Overview](https://docs.dataforseo.com/v3/dataforseo_labs-google-domain_rank_overview-live/)；可按官方文档核对端点权限与返回字段。

自然排名、站点指标、关键词指标和竞品查询会根据 [DataForSEO Labs 市场和语言目录](https://docs.dataforseo.com/v3/dataforseo_labs/locations_and_languages/) 校验查询范围。支持的组合使用目录中的市场编号和语言代码。不支持的组合会在发起付费查询前提示覆盖范围，工作流保留其余证据。有效组合返回空样本时，结论限定在本次查询的数据范围内。

Semrush 竞品工作流按根域、地区和桌面数据库查询月度结果。指定地区时使用两位国家代码，例如 `US` 或 `GB`，省略则使用站点市场。配置密钥后才会注册该工作流。通过统一数据接口单独查询时，在 `params` 指定 `preferred_provider: "semrush"` 和 `country` 即可。数据按地区统计，语言筛选适用于支持该条件的其他数据源。[Semrush v4 Domain Analytics](https://developer.semrush.com/api/v4/seo/domain-analytics/)

业务通知与发布还有各自的配置前提：

- [邮件通知](src/seo_agent/notifications/email.py)需要 `SMTP_HOST`、`SMTP_PORT`、`SMTP_SECURITY`、`SMTP_USERNAME`、`SMTP_AUTH_CODE` 和 `EMAIL_FROM_ADDRESS`；邮箱需开通 SMTP 并取得授权码，端口和加密方式应与邮箱服务匹配。
- [本地 HTML 发布](src/seo_agent/publishing/html_site.py)使用 `HTML_SITE_ROOT_PATH`、`HTML_SITE_BASE_URL` 和可选的 `HTML_SITE_CONTENT_SUBDIR`，不需要第三方 Key。发布目录须可写，并由用户配置的站点服务通过对应 Base URL 提供访问；文件落盘不等于线上地址已经可达。

未配置的 SEO 数据源不进入正式对话的 live 工具列表。DataForSEO 的兼容 pseudo 数据也不会进入正式诊断；页面体检要求性能检查但 PageSpeed 未配置时，会保留页面证据并返回部分完成。

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

对话、业务、证据快照、提示词和观测记录使用 PostgreSQL。填写本地 `.env.local` 的 `POSTGRES_PASSWORD` 和 `DATABASE_URL`，两处使用同一口令。项目自带的 Compose 默认只在本机 `55432` 端口开放数据库。若已有独立 PostgreSQL 实例，也可直接将 `DATABASE_URL` 指向它，数据库用户需要在目标 schema 中建表的权限。

macOS / Linux 使用项目自带数据库时，在仓库根目录执行

```bash
docker compose --env-file .env.local up -d --wait db
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
docker compose --env-file .env.local --profile app up -d --build api
```

容器内的 API 通过服务名 `db` 连接 PostgreSQL。`postgres-data` 卷保存数据库，`artifacts` 卷保存图片和交付文件。

前端另开终端启动：

```bash
cd frontend
npm ci
npm run dev
```

未配置可选数据源或 Ahrefs 仍为 pseudo 模式时，`/health` 的总体状态可能为 `degraded`，可查看各数据源状态。

### 可选：Langfuse Cloud

在 `.env.local` 中配置 `LANGFUSE_HOST`、`LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_SECRET_KEY` 并重启后端。Host 需对应项目所在区域；本地观测保存在 PostgreSQL。

云端记录每轮模型输入（包括用户问题和系统提示词）、可见回答、工具选择及输入输出、执行或复用、结束原因、状态、耗时和模型返回的 Token 用量。组合工作流还会展示实际执行的子流程与数据工具，PRD 和交付审核也保留模型调用记录。模型内部推理不在记录中。

记录可能包含站点和业务数据并发送到云端；常见凭据会脱敏，但不能保证识别所有敏感内容，超长内容会被截断。上传在后台进行，网络故障或程序过早关闭可能导致云端缺少记录，启用前的旧记录不会自动回填。

## 目前的边界

- 综合诊断覆盖代表单页、站点文件和有限搜索样本。Google 实际收录、完整搜索历史和整站抓取仍不在检查范围内。机会入池通过独立接口显式触发，完整“诊断 → 机会审批 → 执行 → 复检”仍需真实端到端验收。
- 当前部署按单个 API 进程运行，进程负责流式事件和本地调度。GSC 与 GA4 目前没有数据适配器。

## 测试

数据库集成测试从 `TEST_DATABASE_URL` 连接专用 PostgreSQL 测试库，每个用例在独立 schema 中运行，完成后清理该 schema。使用项目自带容器时，首次创建一次测试库。随后导出指向该测试库的连接地址，并运行增量用例。

```bash
docker compose --env-file .env.local exec db createdb -U seo_agent seo_agent_test
export TEST_DATABASE_URL='postgresql://seo_agent:your-local-password@127.0.0.1:55432/seo_agent_test'
.venv/bin/python -m pytest -q tests/agent/test_workspace_chat.py tests/healthcare_agent tests/seo_agent/test_conversation_storage.py
```

商旅增量用例覆盖候选缺价、路线、天气、局部重查和同一运行的草稿确认。测试用例不消耗第三方额度。

```bash
.venv/bin/python -m pytest -q tests/agent/test_travel_{flyai,context,plan}.py tests/agent/test_workspace_scenarios.py
```

采购回归覆盖签名、权限和限流，以及本地商品库 1,000 条记录的回包格式、筛选分页和两小时缓存。对话测试覆盖查商品、改量重算、清单估算以及到货与付款去重。

```bash
.venv/bin/python -m pytest -q tests/agent/test_local_catalog.py tests/agent/test_procurement.py tests/agent/test_workspace_scenarios.py -k 'procurement or local_catalog'
```

运行考研检索评测并导出逐题回执：

```bash
.venv/bin/python -m src.healthcare_agent.postgraduate_eval --output /tmp/postgraduate-rag-report.json
```

页面诊断回归覆盖健康页、缺 Title、重复 Canonical 和 noindex，以及 robots.txt 和 sitemap 读取。复检用例先保存缺 Title 的页面基线，确认目标未达成，再模拟同一页面的标题修复并重新核对结果。以下测试可在本地复跑，无需第三方 API Key。

```bash
.venv/bin/python -m pytest -q -s tests/seo_agent/test_lab_diagnosis.py
```

```bash
.venv/bin/python -m pytest \
  tests/agent tests/agent_evals \
  tests/seo_agent/test_geo.py \
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

站点专属 SEO 兼容 API 另有一组固定业务问题评测。配置 DeepSeek 并启动本地后端后，将 `<测试站点 ID>` 换成已初始化的测试站点 ID。

```bash
.venv/bin/python -m src.agent.evals.seo_chat --site-id '<测试站点 ID>'
```

报告记录选中的工具、证据来源、需逐字核对的字段、终态、耗时和对话模型 Token。评测需要的工具未注册，或必需工具因上游失败拿不到证据时，记为 `BLOCKED`。加 `--case tdk` 可单独复跑一题。

## 开源许可与致谢

本项目采用 [MIT 许可证](LICENSE)。

感谢 Seth Black 和贡献者开源 `python-seo-analyzer`。本项目的可选 HTML 基础扫描复用了它的词数统计、标题层级、内链和图片检查。[上游项目](https://github.com/sethblack/python-seo-analyzer)
