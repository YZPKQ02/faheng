# 法衡：多智能体劳动争议法律咨询助手

面向中国大陆法律小白的纵向 MVP。系统通过有状态工作流采集事实、检索权威依据、生成双方观点和风险分析，并提供劳动仲裁模拟与文书草稿。

> 本项目仅提供法律信息和决策辅助，不构成律师意见，不承诺案件结果。内置法条仅用于 MVP 演示；生产使用前必须建立持续更新、逐条校验的官方法律知识库。

## 当前能力

- FastAPI 案件、消息、证据、分析、模拟、文书、引用和反馈 API
- LangGraph 混合接待流程：观察上下文 → 生成粗粒度计划 → 分步执行 → 有界 ReAct 法律检索 → 校验并回答
- 当前问题、初始诉求、最近 12 条消息、事实状态与证据摘要分层装配，避免长对话丢失焦点
- 用户陈述、已确认事实、模型推断分级存储
- 内置现行劳动法律样本及按生效/失效日期过滤的混合关键词检索
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
- PostgreSQL 部署需配置迁移、pgvector、中文全文检索、reranker 和备份。
- 增加认证、租户隔离、加密对象存储、删除/导出数据和日志脱敏。
- 建立 100–200 个律师审核案例，再进入封闭试用。
