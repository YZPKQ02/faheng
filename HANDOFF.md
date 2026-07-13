# AI-Legal-Advisor 项目交接文档

更新时间：2026-07-13（Asia/Shanghai）

## 1. 新会话必须先知道的工作约定

项目目录：`D:\MyCodex\AI-Legal-Advisor`

必须遵守用户给出的约定：

- 修改 JavaScript/TypeScript 文件后必须运行 `npm test`（本项目前端目录为 `web`，Windows 下建议执行 `npm.cmd test`）。
- 安装依赖时优先使用 `pnpm`。
- 添加新的生产依赖前必须先询问用户确认。
- 不要读取、打印、提交或覆盖 `.env` 中的密钥。
- 工作区可能包含用户自己的改动，禁止使用 `git reset --hard`、`git checkout --` 等破坏性命令。

## 2. 我们在做什么

这是一个面向中国大陆劳动争议场景的多智能体法律咨询项目，产品名为“法衡”。目标不是做一个只会聊天的法律机器人，而是实现可追溯、证据驱动、能够进入专业复核流程的案件决策辅助系统。

核心产品流程为：

1. 接收用户陈述并持续维护案件上下文。
2. 区分用户陈述、证据支持、已确认事实、模型推断和争议事实。
3. 从有效法律依据中检索可核验内容。
4. 由劳动者代理、用人单位代理、中立裁判、安全审查等智能体完成双方推演。
5. 输出争点、构成要件、事实/证据/法条链路、不确定性和下一步行动。
6. 高风险、低证据覆盖或时间线冲突时转入人工专业复核。

最近一轮任务重点是解决咨询会话中的两个问题：

- 模型追问出现 `1. 1.`、`2. 2.` 这样的重复序号。
- 长对话中模型忘记用户当前问题，需要改成受控的 ReAct 流程，先形成计划、再执行检索、最后围绕当前问题回答。

## 3. 已经完成的工作

### 3.1 事实与上下文管理

- `FactStatus` 已支持：`user_stated`、`evidence_supported`、`confirmed`、`inferred`、`disputed`、`unknown`。
- 新增事实或证据后，旧分析会自动失效，并记录失效原因。
- 用户可以在前端确认事实或标记“有争议”。
- 新增 `app/conversation_memory.py`，按层次装配：
  - 当前用户消息（最高优先级，强制保留）；
  - 初始诉求；
  - 最近 12 条历史消息；
  - 最多 30 条事实及其状态；
  - 最多 20 条证据摘要。
- 上下文采用限长和中间截断策略，避免消息无限增长，同时保留长消息的开头和结尾。
- 对话历史通过显式 SQL 查询获取，不再依赖可能已经缓存且过期的 SQLAlchemy relationship。

### 3.2 ReAct 咨询工作流

`app/workflow.py` 已从原来的“分诊 → 调查 → 检索 → 回答”改造成 `react-v1`：

1. `observe`：读取当前问题、初始诉求、历史、事实和证据，识别案件类型、紧急程度和信息缺口。
2. `reason`：生成简短、可审计的 `ConversationPlan`，包含问题焦点、用户意图、事实引用、信息缺口、行动类型和检索式。
3. `act`：保存新事实、执行法律检索，并记录检索到的 authority ID。
4. `respond`：把当前用户消息再次固定为最高优先级，仅根据检索结果输出最终答复。

重要边界：系统不会展示或存储模型的详细思维链。审计中只保存可验证的简短计划、输入引用、行动和观察结果。前端可以显示“观察、规划、检索、答复”的阶段状态，但不能展示所谓的原始 CoT。

模型未配置或调用失败时会进入确定性回退，仍然保留当前问题焦点、案件类型、法律依据和下一步信息缺口。

### 3.3 重复序号修复

- 后端 `_format_follow_up_questions` 会先递归清除模型自带的 `1.`、`2、`、`（1）` 等前缀，然后由系统统一编号、去重并限制为最多 3 条。
- 前端 `web/lib/format.ts` 对数据库中已经存在的 `1. 1.`、`2. 2、` 历史消息做显示层兼容修复。
- `web/app/page.tsx` 对已保存回复和流式回复都应用了该规范化函数。

### 3.4 RAG 与推理准确性

- `app/authorities.py` 已实现确定性混合检索，包括中文 n-gram、法律层级权重、地区和有效日期过滤。
- `app/reasoning.py` 已覆盖违法解除、未签劳动合同、加班费、欠薪、经济补偿、仲裁时效等争点规则。
- 推理链可以关联：争点 → 构成要件 → fact ID/evidence ID → authority ID。
- 已加入时间线冲突检查、引用支持校验、质量指标、置信度校准和决策门禁。
- 置信度在产品中表示“证据成熟度/分析可靠程度”，不是胜诉概率。

### 3.5 多智能体治理与人工复核

- 已有 `AgentTask` 和 `HumanReviewTask` 数据模型。
- 策略工作流会记录 4 个 `agent-task-v1` 任务：
  - `worker_advocate`
  - `employer_advocate`
  - `neutral_adjudicator`
  - `safety_reviewer`
- 案件阶段由协调器统一维护。
- 证据不足、引用缺失、时间线冲突等情况会创建人工审核任务。
- 人工审核支持批准、驳回、要求修改；驳回和要求修改会使当前分析失效。
- 已实现接口：
  - `GET /cases/{case_id}/agent-tasks`
  - `GET /cases/{case_id}/reviews`
  - `POST /reviews/{review_id}/decision`
  - `PATCH /cases/{case_id}/facts/{fact_id}`

### 3.6 案例数据和评测

- 已有评测框架 `app/evaluation.py` 和脚本 `scripts/evaluate_agents.py`。
- 已有示范数据：
  - `data/evaluation/gold_cases.json`
  - `data/evaluation/official_model_labeled_cases.json`
- 后一个数据集来源于最高人民法院公开案件，由模型模拟专业人员生成结构化标注。
- 这些标注必须继续保持 `pending_professional_review` 语义，不能对外宣称为律师标注，也不能用于声称真实业务准确率。
- 已有官方来源白名单、页面低频获取、脱敏、内容哈希去重和案例导入流程。

### 3.7 前端产品体验

- `web/app/page.tsx` 和 `web/app/globals.css` 已完成一次完整的高标准重构。
- 当前设计采用深海军蓝、暖金、米白和语义状态色，兼顾法律行业的可信度与可读性。
- 已实现：
  - 案件列表、置顶、删除；
  - 欢迎页和快速场景；
  - 流式咨询；
  - 事实确认/争议标记；
  - 证据登记；
  - 当前/失效分析区分；
  - 人工复核提示；
  - 争点与证据链报告；
  - 仲裁模拟；
  - 字数限制、可访问性标签、响应式布局、减少动效适配。
- 已做过 1440×1000 桌面端和 390×844 移动端视觉检查，移动端标题截断问题已修复。

### 3.8 当前验证状态

最近一次完整验证结果：

- `ruff check app tests`：通过。
- `pytest`：23 项通过，1 个既有 Starlette/httpx 弃用警告。
- `npm.cmd test`：5 项通过。
- `npm.cmd run build`：通过。
- Next.js 首页生产构建约 9.41 kB，First Load JS 约 111 kB。
- 没有新增生产依赖。

## 4. 当前卡在哪里

目前没有阻止本地继续开发的代码级 blocker；最近提出的重复序号和上下文遗忘问题已经修复并通过测试。

真正阻碍项目生产落地的是外部和工程化条件：

- 缺少足量律师真实复核的金标准数据，无法严肃声明准确率、召回率或校准水平。
- 法律知识库仍包含 MVP 演示内容，需要完整的官方法规版本管理、地区规则、失效追踪和专业复核。
- 尚未完成认证、租户隔离、授权、数据导出/删除、日志脱敏、加密对象存储、备份和灾难恢复。
- 尚未建立模型供应商级的成本限额、超时、熔断、并发控制、观测指标和真实生产压测。
- SQLite 适合本地演示；生产需要 PostgreSQL、正式 Alembic 迁移，以及后续是否采用 pgvector/全文检索/reranker 的技术决策。
- ReAct 当前为单轮“规划 → 检索 → 回答”，尚未实现基于观察结果自动判断是否需要再次检索的有限循环，也没有对每一步做离线轨迹评估。
- 尚缺端到端浏览器测试，当前前端主要是 API 单测、格式化回归测试和生产构建检查。

已有但不阻塞的警告：

- Vitest 会提示 Vite Node API 的 CJS 构建弃用。
- pytest 会提示 Starlette TestClient 使用当前 httpx 的弃用警告。
- 不要为了清理警告直接升级生产依赖；先评估兼容性并向用户确认新增/调整生产依赖的权限。

## 5. 下一步计划（按建议优先级）

### P0：验证当前会话修复

1. 用真实 DeepSeek 配置跑 20–30 轮多轮对话回归，覆盖：只回答“没有/有/是”、突然切换诉求、纠正旧事实、超长描述、模型自带序号。
2. 增加“当前问题命中率”评测：人工标注每轮 `question_focus`，检查最终答复是否直接回应。
3. 增加追问重复率、已回答信息重复询问率、无依据法条率和上下文事实冲突率指标。
4. 检查 `react_plan`/`react_action` 审计内容是否需要进一步脱敏或仅保存哈希与引用。

### P1：完善受控 ReAct

1. 在固定最大步数（建议最多 2 次检索）内支持“观察结果不足 → 改写检索式 → 再检索”。
2. 所有循环必须有步数、成本、超时和 token 上限，禁止无界 Agent 循环。
3. 增加回答前校验器：当前问题覆盖、事实状态一致、引用均来自检索结果、追问未重复、风险提示适当。
4. 把计划、行动、观察和最终回答纳入离线轨迹评测，而不是只评价最终文本。

### P1：建立可声明准确率的评测集

1. 邀请律师复核 100–200 个真实、已脱敏劳动争议案例。
2. 双人独立标注争点、要件、事实状态、证据、适用条文、结论和不确定性；冲突由第三人仲裁。
3. 切分训练/开发/测试集，并锁定测试集版本。
4. 分别报告检索召回、引用正确率、事实一致性、争点识别、要件覆盖、校准误差和人工升级召回，不能只报一个“准确率”。

### P2：生产安全与数据治理

1. 增加账户、租户、RBAC 和案件级授权。
2. 对消息、证据文件和审计日志进行分级加密、脱敏和保留期限管理。
3. 实现案件数据导出和可验证删除。
4. 对提示注入、越权读取、跨案件泄漏、恶意附件和日志泄密做安全测试。
5. 建立律师人工复核工作台、SLA、复核意见回流和版本追踪。

### P2：部署与可观测性

1. 切换 PostgreSQL 并引入正式 Alembic 迁移。
2. 增加请求 trace ID、模型调用耗时/成本、RAG 命中、回退率、人工升级率和错误率监控。
3. 建立开发、预发布、生产环境隔离及数据库备份恢复演练。
4. 补充 Playwright 等端到端测试前，若需要新增生产或开发依赖，先确认用户意愿并优先用 pnpm。

## 6. 已踩过的坑：绝对不要再踩

### 6.1 不要让模型和渲染层同时拥有编号

重复序号的直接原因是模型返回 `1. 问题`，后端又加了一次 `1.`。规则必须是：模型输出无序号的问题文本，后端是唯一编号来源；前端只做旧数据兼容，不承担新消息编号逻辑。

### 6.2 不要通过已加载的 relationship 获取刚写入的新消息

原实现先通过 `selectinload` 加载 `case.messages`，之后只用 `case_id` 插入新 Message。SQLAlchemy 已加载的 relationship 不一定自动包含这条新消息，导致模型看不到当前问题。必须显式查询消息，或显式维护 relationship；当前实现采用显式查询并独立固定当前消息。

### 6.3 不要只把“最近对话”作为一个 Python repr 塞给模型

当前问题必须使用独立字段并声明最高优先级；初始诉求、历史、事实和证据必须结构化分层。否则短回答（例如“没有”）会丢失指代，长对话会转而回答旧问题。

### 6.4 不要把 ReAct 等同于展示思维链

可以展示阶段、问题焦点、行动和可核验结果，但不要要求、存储或展示详细内部推理。详细 CoT 既不可稳定验证，也会带来隐私、提示注入和产品风险。可审计的是结构化计划及事实/证据/法条引用。

### 6.5 不要把模型模拟标注宣传成律师标注

`official_model_labeled_cases.json` 可以用于流水线和初步评测，但必须标明待专业复核，不能用于对外承诺准确率或胜诉预测。

### 6.6 不要把置信度写成胜诉概率

当前样本量、标注质量和校准条件不足。前端已经将其描述为证据成熟度；后续也必须保持这一边界。

### 6.7 不要让智能体自由无限循环

多智能体和 ReAct 都必须有固定协议、最大步数、超时、成本上限、输入引用和失败回退。不要实现没有边界的“自主讨论直到满意”。

### 6.8 不要绕过事实状态直接把模型输出写成 confirmed

模型抽取的内容默认最多是 `user_stated` 或 `inferred`。只有用户确认、证据支持或专业复核后才能升级状态。

### 6.9 不要在新增材料后继续展示旧分析为当前结论

新增事实、修改事实状态或新增证据后必须调用分析失效逻辑。前端只能把 `is_current=true` 的结果当作当前报告。

### 6.10 不要直接使用 `npm` PowerShell 脚本

本机执行策略可能阻止 `npm.ps1`，使用 `npm.cmd test`、`npm.cmd run build`。Vitest 在沙箱中可能因为无法写入 `web/node_modules/.vite/vitest/results.json` 报 `EPERM`；这不是测试逻辑失败，必要时申请在沙箱外运行，不要通过删除依赖目录或关闭测试缓存来规避。

### 6.11 不要从 Next.js 页面入口导出任意测试函数

曾把 `normalizeConsultationContent` 直接从 `web/app/page.tsx` 导出，单测通过但 `next build` 因页面模块存在不允许的额外导出而失败。工具函数必须放在 `web/lib/format.ts` 等普通模块中。

### 6.12 不要依赖当前 Git 状态做破坏性操作

此前 `git status` 曾返回“not a git repository”，尽管目录可见 `.git`。不要假设 Git 元数据完整，也不要执行 reset/clean/checkout 来“恢复”工作区。优先直接检查文件并使用 `apply_patch` 做局部修改。

### 6.13 不要输出密钥或 `.env` 内容

DeepSeek 密钥只应保存在本机 `.env`。诊断配置时只检查是否已配置，禁止打印具体值。

## 7. 关键文件索引

- `app/workflow.py`：咨询 ReAct 主流程、追问编号、模型回退。
- `app/conversation_memory.py`：分层上下文装配和长度控制。
- `app/agent_contracts.py`：模型结构化输出协议，包括 `ConversationPlan`。
- `app/model_gateway.py`：DeepSeek 结构化调用、重试和校验。
- `app/authorities.py`：法律依据检索。
- `app/reasoning.py`：争点、要件、链路和质量指标。
- `app/strategy_workflow.py`：四智能体策略工作流。
- `app/coordinator.py`：案件阶段与人工复核协调。
- `app/analysis_lifecycle.py`：旧分析失效。
- `app/main.py`：FastAPI 接口和流式状态。
- `web/app/page.tsx`：主要前端交互。
- `web/app/globals.css`：前端视觉系统和响应式布局。
- `web/lib/api.ts`：前端 API 客户端和类型。
- `web/lib/format.ts`：历史重复序号兼容修复。
- `tests/test_workflow.py`：ReAct、当前问题固定和编号回归测试。
- `tests/test_api.py`：主要 API、分析门禁和人工审核测试。
- `web/app/page.test.tsx`：前端 API 与序号兼容测试。
- `README.md`：运行、评测和治理说明。

## 8. 可执行命令

从项目根目录运行后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

另开终端运行前端：

```powershell
cd web
pnpm dev
```

后端验证：

```powershell
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m pytest
```

前端验证：

```powershell
cd web
npm.cmd test
npm.cmd run build
```

运行智能体评测：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_agents.py --output data\evaluation\report.json
```

运行公开案例模型标注集评测：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_agents.py --dataset data\evaluation\official_model_labeled_cases.json --output data\evaluation\official_report.json
```

## 9. 新会话建议的第一步

先阅读本文件和 `README.md`，然后运行后端测试、前端测试与生产构建，确认基线没有变化。若用户继续要求优化，优先推进“真实模型多轮对话回归 + 当前问题命中率/重复追问率评测”，不要直接继续堆 UI 或增加更多智能体。
