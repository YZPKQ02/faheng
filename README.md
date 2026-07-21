# 法衡：多智能体劳动争议法律咨询助手

面向中国大陆法律小白的纵向 MVP。系统通过有状态工作流采集事实、检索权威依据、生成双方观点和风险分析，并提供劳动仲裁模拟与文书草稿。

> 本项目仅提供法律信息和决策辅助，不构成律师意见，不承诺案件结果。内置法条仅用于 MVP 演示；生产使用前必须建立持续更新、逐条校验的官方法律知识库。

## 当前能力

- FastAPI 案件、消息、证据、分析、模拟、文书、引用和反馈 API
- LangGraph 混合接待流程：观察上下文 → 生成粗粒度计划 → 分步执行 → 有界 ReAct 法律检索 → 校验并回答
- 当前问题、初始诉求、最近 12 条消息、事实状态与证据摘要分层装配，避免长对话丢失焦点
- 用户陈述、已确认事实、模型推断分级存储
- 内置现行劳动法律样本、文档/版本/条款三级知识结构及按日期和地域硬过滤的关键词检索
- 对方抗辩、证据缺口、置信度、待核实事项和行动建议
- 响应式 Next.js 案件工作台与仲裁沙盘
- SQLite 零配置开发；SQLAlchemy 数据层可切换 PostgreSQL
- DeepSeek V4 Flash 结构化模型网关，失败时安全回退到确定性流程
- 劳动者代理、用人单位代理、中立裁判和安全审查多 Agent 策略图
- 官方来源白名单、二次脱敏、内容哈希去重和待审核案例导入流水线

## DeepSeek 配置

先撤销任何曾公开粘贴的密钥，再把新密钥放入本机 `.env`。不要修改 `.env.example`：

```dotenv
MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=你的新密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

未配置密钥或模型调用失败时，工作流自动使用确定性回退，并在审计事件中记录 `model_enabled=false`。模型只能引用检索阶段提供的 authority id；未知引用会被丢弃。

所有外部模型请求默认经过集中式出站脱敏，确定性遮蔽中国大陆身份证号、手机号、固定电话、邮箱和银行卡号，仅记录脱敏类型及数量，不记录命中的原始值。生产环境应保持 `MODEL_REDACTION_ENABLED=true`。

外部模型调用还要求案件级有效授权。生产环境应保持 `MODEL_CONSENT_REQUIRED=true`，并配置高强度、独立托管的 `PSEUDONYM_HMAC_SECRET`。该密钥用于生成案件和租户作用域内的实体指纹，不应提交到仓库，也不能与 JWT、数据库或模型供应商密钥复用。

授权与假名管理接口：

```text
GET    /cases/{case_id}/model-consents
POST   /cases/{case_id}/model-consents
DELETE /cases/{case_id}/model-consents/{consent_id}
GET    /cases/{case_id}/pseudonyms
POST   /cases/{case_id}/pseudonyms
```

只有案件所有者或管理员可以管理授权与假名。授权按供应商、用途、数据类别和版本保存；重新授权会撤销旧版本，撤销后新的外部模型调用立即进入确定性回退。假名表只保存 HMAC 指纹、原文长度、实体类型和案件内假名，不重复保存实体原文。模型调用审计只记录授权版本、用途、数据类别和假名数量，不保存 prompt 原文。

规则脱敏和显式假名映射仍不等于完整匿名化：未登记的姓名、企业名称、详细地址、罕见职位及自由文本中的间接身份线索仍可能识别当事人。真实业务接入前还需要在用户界面明确展示授权范围，建立字段级数据分类，并完成对模型供应商数据处理与保留策略的审查。

咨询工作流采用 `plan-execute-react-v1` 协议：外层 Plan-and-Execute 使用应用编译的固定步骤契约，模型只能提供问题焦点、信息缺口和检索意图；事实写入等确定性步骤不使用 ReAct，法律检索步骤仅允许 `search_authorities`，每轮最多调用两次。计划、步骤结果、工具预算和最终校验分别写入审计事件，不保存或展示模型的详细思维链。

## 官方案例数据流水线

采集器只允许国家法律法规数据库、最高人民法院、人民法院案例库、中国裁判文书网、中国政府网和人社部域名。它不会发现或遍历链接，也不会绕过登录、验证码或访问频控。

低频保存单个公开页面用于人工整理：

```powershell
.\.venv\Scripts\python.exe scripts\fetch_public_page.py "官方公开页面 URL"
```

将页面整理为 [示例结构](data/cases/example.json)，核对来源并二次脱敏后导入：

```powershell
.\.venv\Scripts\python.exe scripts\import_cases.py data\cases\your-cases.json
```

导入内容按来源 URL 和正文哈希去重，默认状态为 `pending`。只有经专业人员复核并改为 `approved` 的案例才应进入生产检索。`GET /knowledge/stats` 可查看法条、案例和审核状态数量。

## 版本化法律知识库与检索基线

法律材料采用 `LegalDocument → LegalDocumentVersion → LegalChunk` 三级结构：文档保存稳定身份，版本保存生效/失效区间和内容哈希，条款块保存条文定位、关键词及兼容引用 ID。现有 `LegalAuthority` 暂时保留为 API 与分析结果的引用兼容层。迁移会把内置 5 条演示法条自动回填到新结构。

将已经核对官方来源的法律材料整理成 JSONL（每行一个文档版本）后导入：

```powershell
.\.venv\Scripts\python.exe scripts\import_legal_knowledge.py data\legal\your-authorities.jsonl
```

每条记录至少包含 `title`、`level`、`source_url`、`effective_on` 和非空 `chunks`；每个条款块至少包含 `locator` 与 `content`。导入器执行官方域名白名单校验、版本内容哈希去重、条款定位冲突校验和生效区间重叠告警，并写入不含法律正文的 `legal_knowledge_import` 审计事件。新材料默认 `pending`，需专业复核后才能作为生产级法律依据。

运行关键词检索回归基线：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py --output data\evaluation\retrieval_report.json
```

报告同时运行关键词基线和混合检索，包含 Recall@5/10、MRR、nDCG@10、引用有效性、空结果率以及日期/地域越界数，并明确输出混合 Recall@10 是否低于关键词基线。内置数据只用于防止检索链路回归，不代表真实法律问答准确率。

混合检索使用关键词 Top 40 与语义 Top 40，通过 RRF 融合后返回。SQLite 和测试环境把向量保存为 JSON 并在 Python 中计算余弦相似度；PostgreSQL 使用 `pgvector` 的 `vector` 类型和距离运算。生产迁移前必须由数据库管理员安装或授权创建 `vector` 扩展，然后执行 Alembic：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

默认 `EMBEDDING_PROVIDER=deterministic` 只是可重复、无外部调用的哈希向量回退，用于验证链路和测试，不能声称具备生产语义理解。配置兼容 HTTP Embedding 接口后，先离线生成法律条款向量：

```powershell
.\.venv\Scripts\python.exe scripts\index_legal_embeddings.py
```

HTTP 接口约定为 `POST {EMBEDDING_BASE_URL}/embeddings`，请求包含 `model` 和 `input`，响应包含按 `index` 排列的 `data[].embedding`。案件查询在发送到外部 Embedding 服务前会执行规则脱敏，并要求案件存在有效 `analysis` 模型授权，且授权供应商必须等于 `EMBEDDING_CONSENT_PROVIDER`；未授权或调用失败时只回退关键词检索。不得把真实密钥写入仓库。

### 本机 Qwen3-Embedding-8B 配置

当前开发机使用 Qwen 官方 `Qwen3-Embedding-8B-Q4_K_M.gguf` 和 llama.cpp CUDA 12 服务。模型原始输出为 4096 维；项目利用 Qwen3 的 MRL 能力截断并重新归一化为 1536 维入库，以满足 pgvector HNSW 的维度上限。模型与运行时安装在 `D:\AIModels`，不属于仓库内容。启动或确认本地服务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_local_embedding.ps1
```

服务只监听 `127.0.0.1:8080`，使用 Embedding-only、last pooling、4096 上下文和单并发。项目专用的非敏感连接参数保存在被 Git 忽略的 `.env.local`；它覆盖 `.env` 中的同名配置，但不会读取或修改已有密钥。由于服务完全位于本机回环地址，本地配置设置 `EMBEDDING_CONSENT_REQUIRED=false`；切换为远程服务时必须恢复为 `true` 并配置匹配的授权供应商。

Qwen3 Embedding 的查询侧会自动添加面向中国劳动争议法条检索的英文 instruction，法律条文入库侧不添加 instruction。首次导入新法条后重新生成向量：

```powershell
.\.venv\Scripts\python.exe scripts\index_legal_embeddings.py
```

当前 5 条演示法条上的真实 Qwen 基线报告位于 `data/evaluation/retrieval_report_qwen3_8b.json`：混合 Recall@10 为 1.0，关键词为 0.875。样本量极小，且 Top 5 已接近全部语料，该结果只能用于连接与回归验证，不能作为生产检索准确率。

### 1000 条级官方法规试点库

试点数据库使用 `compose.trial.yml`，只监听本机 `127.0.0.1:5433`，不得复用其免密配置到生产环境。规范化语料和来源清单位于 `data/legal/pilot/`，原始网页缓存在被 Git 忽略的 `data/raw/xzfg/`。重新构建、导入和索引：

```powershell
docker compose -f compose.trial.yml up -d
.\.venv\Scripts\python.exe scripts\build_xzfg_pilot.py --target-min 1000 --target-max 1500
$env:DATABASE_URL='postgresql+psycopg://legal_trial@127.0.0.1:5433/legal_trial'
.\.venv\Scripts\python.exe scripts\import_legal_knowledge.py data\legal\pilot\corpus.jsonl
.\.venv\Scripts\python.exe scripts\index_legal_embeddings.py
```

当前清单包含司法部国家行政法规库的 25 部现行行政法规、1092 个自然法条块；同名历史版本和无法可靠解析的页面只进入失败/人工复核清单。所有记录初始 `review_status=pending`，完成法律专业复核前不能标记为生产可用。

解析器勘误后的非覆盖式语料版本位于 `data/legal/pilot_v2/`，包含相同 25 部法规和 1093 个自然块；《女职工劳动保护特别规定》的“附录”已从第十六条拆为独立 locator。导入已有试点库时必须同时提供与语料 SHA-256 绑定的转换清单：

```powershell
.\.venv\Scripts\python.exe scripts\import_legal_knowledge.py data\legal\pilot_v2\corpus.jsonl --transition-manifest data\legal\pilot_v2\transition_manifest.json
```

版本转换只接受两种显式类型：`correction` 用于采集或解析勘误，旧版本标记为 `superseded` 且不再参与任何日期的检索；`amendment` 用于正式修订，旧版本在新版生效日转为 `expired`，仍可按历史日期检索。未提供精确旧内容哈希和转换类型的重叠版本会被拒绝，不会只告警后继续产生多个 active 版本。PostgreSQL 导入前必须先执行并检查 Alembic 迁移；导入脚本不会对 PostgreSQL 调用 `create_all`。

### 全国人大现行法律首批语料

`data/legal/npc_laws_v1/` 包含国家法律法规数据库中的 8 部劳动相关现行法律、658 个自然法条块：劳动法、劳动合同法、劳动争议调解仲裁法、社会保险法、就业促进法、工会法、妇女权益保障法和职业病防治法。构建器使用全国人大公开搜索/详情接口核验唯一现行版本，下载官方 DOCX，并同时校验标题、第一条、预期末条和法条总数；原始文件缓存在 Git 忽略的 `data/raw/npc_laws/`。

```powershell
.\.venv\Scripts\python.exe scripts\build_npc_laws.py
.\.venv\Scripts\python.exe scripts\import_legal_knowledge.py data\legal\npc_laws_v1\corpus.jsonl --transition-manifest data\legal\npc_laws_v1\transition_manifest.json
.\.venv\Scripts\python.exe scripts\index_legal_embeddings.py
```

转换清单将内置演示版本与官方全文显式衔接。所有新版本仍为 `pending`，在完成专业复核前不得标记为生产可用，也不得据此修改现有冻结检索测试集。

### 跨阶段劳动者代理记忆

- 对外统一角色为 `worker_counsel`（劳动者代理），诉求收集和仲裁场外辅导使用同一身份。
- `GET /cases/{case_id}/worker-counsel-memory` 返回当前代理档案及版本号；档案包含诉求、事实及其状态、证据、当前分析、待补问题和来源引用。
- 新建仲裁模拟时会固定一份代理档案快照和版本号。案件后续更新不会静默改写已经开始的模拟。
- 当前仅开放 `coach` 场外辅导模式：劳动者仍由用户本人扮演，代理不得确认、推测或陈述用户未确认的新事实。
- 仲裁阶段每轮最多展示 4 条表达/举证重点和 4 条可直接填入回答框的建议回答；填入只替换草稿，不会自动发送，方括号占位内容必须由用户核对。
- 仲裁庭中的单位代理、仲裁员等可见发言通过 SSE 按角色逐段推送；每个结构化模型结果通过校验后才进入庭审记录。
- 升级既有数据库后可运行 `python scripts/backfill_worker_counsel_memory.py` 为历史案件建立首版档案，并为尚未绑定档案的 active 模拟固定一次交接快照；已完成的历史模拟不会被追溯改写。

### 120 题检索金标准草案

运行 `scripts/build_retrieval_gold.py` 可从试点语料生成可追溯的待审草案。输出位于 `data/evaluation/pilot_gold/`：80 题开发集、40 题冻结测试集，8 个主题各 15 题，并包含证据摘录、官方链接和数据集 SHA-256。`scripts/tune_hybrid_retrieval.py` 只能用开发集选参，选定后才读取一次冻结集；`scripts/analyze_retrieval_errors.py` 生成逐题漏检报告。

当前开发集仍选择默认 RRF 参数（关键词 1.0、语义 1.0、k=60）。冻结集 Recall@10 为：关键词 0.925、纯语义 0.925、混合 0.900。该结果表明现有等权 RRF 在跨法规问题上可能把一条正确依据挤出 Top 10，不能假定“混合必然优于关键词”。草案全部为 `review_status=pending`，正式发布前按 `REVIEW_GUIDE.md` 进行法律专业审核。

AI 法律证据审核后的 v2 位于 `data/evaluation/pilot_gold/reviewed_v2/`。120 题中通过 116 题（88 题原样通过、28 题修改后通过），剔除 4 题；其中包括一题因附录被错误归入第十六条而拒绝。v2 保留 76 题开发集和重新冻结的 40 题测试集。冻结集 Recall@10：关键词 0.950、纯语义 0.9875、混合 0.975；混合相对关键词 39 题持平、1 题更好、0 题更差。该审核属于 AI 证据与法律一致性审核，不构成执业律师签署，也不表示已穷尽上位法、司法解释、部门规章和地方规则。

最终回答的 RAG 上下文只来自通过日期、地域和版本状态校验的条款块，包含可核验来源、版本标签和生效区间。模型只能返回候选 `authority_id`；应用会再次校验 ID 是否属于本轮候选及当前有效范围，拒绝项写入审计且不会成为“可核验依据”。

## 本地运行

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

另开终端：

```powershell
cd web
pnpm install
pnpm dev
```

访问 `http://localhost:3001`，API 文档位于 `http://localhost:8000/docs`。`pnpm dev` 和 `pnpm start` 均固定使用前端端口 3001。

后端默认接受来自 `http://localhost:3001` 和 `http://127.0.0.1:3001` 的浏览器跨域请求。若前端运行在其他地址或端口，请通过逗号分隔的 `CORS_ORIGINS` 配置明确允许的来源；不要在携带凭证的环境中配置通配来源。

## 认证与租户隔离

本地开发默认 `AUTH_ENABLED=false`，请求使用仅限本机开发的 `local` 身份。任何共享、预发布或生产环境都必须启用 OIDC/JWT 验证：

```dotenv
AUTH_ENABLED=true
OIDC_ISSUER=https://你的身份提供商/
OIDC_AUDIENCE=legal-advisor-api
OIDC_JWKS_URL=https://你的身份提供商/.well-known/jwks.json
OIDC_ALGORITHMS=RS256
OIDC_TENANT_CLAIM=tenant_id
OIDC_ROLES_CLAIM=roles
```

服务端固定校验算法白名单、签名、issuer、audience、`exp`、`iat` 和 `sub`。令牌还必须包含租户标识。普通用户只能访问本人案件；`admin` 和 `reviewer` 可访问同租户案件；人工复核决定要求 `admin`、`reviewer` 或 `lawyer` 角色，审核人身份以令牌 `sub` 为准。

浏览器端不应保存 OIDC 客户端密钥或服务端静态 API 密钥。部署时应通过受信任的 OIDC 登录流程取得用户访问令牌；正式 PostgreSQL 部署还必须使用 Alembic 添加 `case_files.tenant_id`、`case_files.owner_id` 及相应索引。

## 验证

```powershell
pytest
ruff check app tests
cd web
npm test
npm run build
```

## 数据库迁移

新建环境应通过 Alembic 创建数据库结构，不要依赖应用启动时的 `create_all`：

```powershell
$env:DATABASE_URL="postgresql+psycopg://用户名:密码@主机:5432/数据库名"
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic check
```

当前迁移基线位于 `migrations/versions/`，包含完整业务表、外键和租户/所有者索引。`alembic check` 应输出 `No new upgrade operations detected`。

PostgreSQL 使用与当前同步 SQLAlchemy `Session` 一致的 `psycopg` 驱动。连接池可通过 `DATABASE_POOL_SIZE`、`DATABASE_MAX_OVERFLOW` 和 `DATABASE_POOL_TIMEOUT_SECONDS` 配置，并启用失效连接预检查。应用仅在 SQLite 本地模式自动建表；PostgreSQL 环境必须在启动 API 前完成 `alembic upgrade head`。

已有的本地 SQLite MVP 数据库由应用中的兼容逻辑维护，不要直接对其执行初始迁移，也不要未经核对就执行 `alembic stamp head`。任何存量生产数据库接入 Alembic 前必须先备份、比较实际 schema 与迁移基线，并制定单独的基线接管迁移。生产环境禁止在请求服务启动过程中自动修改数据库结构。

## 智能体评测

运行可重复的争议焦点、法条召回、要件依据和引用有效性评测：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_agents.py --output data\evaluation\report.json
```

内置 `gold_cases.json` 是评测链路示范数据，不代表真实案件准确率。对外报告准确率或校准结果前，应将案例标记为 `lawyer_labeled`，补充专业人员标注的争议焦点、适用法条和实际裁判结果。

`official_model_labeled_cases.json` 收录最高人民法院公开真实案例，并由模型模拟专业人员完成结构化标注。它可直接用于 RAG 与链路评测，但默认状态为 `pending_professional_review`，不参与真实概率校准：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_agents.py --dataset data\evaluation\official_model_labeled_cases.json --output data\evaluation\official_report.json
```

### Harness 质量门禁

离线 Harness 使用固定日期、临时 SQLite 和确定性 embedding，验证策略图协议、引用边界、检索指标及故障回退，并与受审查基线比较：

```powershell
.\.venv\Scripts\python.exe scripts\run_harness.py run --profile offline --output artifacts\harness\offline
```

只有完整且规则门禁通过的离线报告才能显式更新基线：

```powershell
.\.venv\Scripts\python.exe scripts\run_harness.py baseline accept --report artifacts\harness\offline\report.json
```

真实模型 Harness 仅允许使用三个合成/公开案例，且必须显式提供收费调用开关、有效授权清单和调用预算。`data/harness/live_authorization.example.json` 只是模板，不能直接执行：

```powershell
.\.venv\Scripts\python.exe scripts\run_harness.py run --profile live --allow-paid-model --authorization <已批准的授权清单> --max-model-calls 15 --max-http-requests 30 --output artifacts\harness\live
```

LLM Judge 首期只生成告警，不作为合并门禁或律师意见。Harness 报告不保存原始 prompt、密钥或完整模型回答。

## 可观测性基线

系统将咨询模型调用与法律检索的非敏感性能指标写入审计事件。模型指标包括阶段、结果、耗时、尝试/重试次数、供应商状态码、错误类型及脱敏数量；检索指标包括候选数、有效候选数、结果数、耗时和可选的查询指纹。指标不保存 prompt、用户查询原文、模型输出或敏感实体。

生产环境应配置独立的 `OBSERVABILITY_HMAC_SECRET`，用于生成租户作用域的查询指纹；未配置时不会保存查询指纹，也就无法计算重复查询率。该密钥不能与 JWT、假名或模型供应商密钥复用。

具备人工复核权限的用户可以读取最近 1–168 小时的租户聚合指标：

```text
GET /internal/metrics?hours=24
```

该接口返回模型调用/回退/重试/平均耗时，以及检索次数、空结果、平均耗时和重复查询率。应先根据这些数据确认真实瓶颈，再决定是否引入 Redis：重复检索率持续较高时考虑缓存；多实例部署或重复付费调用出现后，再考虑分布式限流、幂等键和案件锁。

## 多智能体生产治理

- 案件协调器根据事实、证据、当前分析和人工审核任务统一维护案件阶段。
- 每个策略 Agent 都生成 `agent-task-v1` 任务信封，记录目标、输入引用、执行约束、输出、状态和耗时。
- 证据覆盖不足、引用缺失或时间线冲突时，系统自动创建人工审核任务。
- 人工审核支持批准、驳回和要求修改；后两种决定会使当前分析失效。

相关接口：

```text
GET  /cases/{case_id}/agent-tasks
GET  /cases/{case_id}/reviews
POST /reviews/{review_id}/decision
```

## 生产化缺口

- 接入真实模型前，实现供应商适配器、结构化输出重试、成本限制和密钥托管。
- 用官方来源替换演示法条，增加版本、地区、条文定位、抓取审计和专业复核。
- PostgreSQL 部署需验证迁移、pgvector 扩展权限与向量查询；中文全文索引、专用法律 reranker 和备份恢复仍待生产化。
- 增加认证、租户隔离、加密对象存储、删除/导出数据和日志脱敏。
- 建立 100–200 个律师审核案例，再进入封闭试用。
