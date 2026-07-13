# 法衡：多智能体劳动争议法律咨询助手

面向中国大陆法律小白的纵向 MVP。系统通过有状态工作流采集事实、检索权威依据、生成双方观点和风险分析，并提供劳动仲裁模拟与文书草稿。

> 本项目仅提供法律信息和决策辅助，不构成律师意见，不承诺案件结果。内置法条仅用于 MVP 演示；生产使用前必须建立持续更新、逐条校验的官方法律知识库。

## 当前能力

- FastAPI 案件、消息、证据、分析、模拟、文书、引用和反馈 API
- LangGraph ReAct 接待流程：观察上下文 → 形成可审计计划 → 执行法律检索 → 围绕当前问题回答
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

咨询工作流采用 `react-v1` 协议：规划阶段只保存问题焦点、行动类型、检索式和上下文引用，不保存或展示模型的详细思维链；行动阶段的检索结果单独写入审计事件。

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

访问 `http://localhost:3000`，API 文档位于 `http://localhost:8000/docs`。

## 验证

```powershell
pytest
ruff check app tests
cd web
npm test
npm run build
```

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
