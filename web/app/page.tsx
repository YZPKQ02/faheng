"use client";

import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import { api, CaseFile, Fact, HumanReview, ModelConsent, Simulation, SimulationLine } from "../lib/api";
import { mergeConsentScope, type ModelPurpose } from "../lib/consent";
import { normalizeConsultationContent } from "../lib/format";

const stageLabels: Record<string, string> = {
  intake: "开始咨询",
  fact_gathering: "事实梳理",
  fact_review: "事实确认",
  evidence_review: "证据补充",
  issue_identification: "争点识别",
  analysis_stale: "分析待更新",
  strategy_ready: "分析完成",
  human_review: "专业复核",
};

const factLabels: Record<string, string> = {
  user_stated: "你的陈述",
  evidence_supported: "有证据支持",
  confirmed: "已确认",
  inferred: "模型推断",
  disputed: "存在争议",
  unknown: "尚不确定",
};

const starters = [
  { icon: "解", title: "突然被辞退", detail: "判断解除是否合法、需要保留哪些材料", text: "公司突然通知我不用来上班，也没有书面说明理由，我该怎么办？" },
  { icon: "薪", title: "拖欠工资", detail: "梳理欠薪期间、工资标准与追索路径", text: "公司拖欠我三个月工资，我仍然在职，有工资流水和聊天记录。" },
  { icon: "约", title: "未签合同", detail: "核对劳动关系与双倍工资适用条件", text: "工作一年多一直没有签劳动合同，我可以主张什么权利？" },
  { icon: "时", title: "加班争议", detail: "识别有效证据与加班费计算基础", text: "公司长期要求加班但没有支付加班费，我应该准备哪些证据？" },
];

type View = "welcome" | "chat" | "report";
type ConsentPurpose = ModelPurpose;
type ConsentRequest = { purpose: ConsentPurpose; content?: string };

export default function Home() {
  const [cases, setCases] = useState<CaseFile[]>([]);
  const [current, setCurrent] = useState<CaseFile | null>(null);
  const [reviews, setReviews] = useState<HumanReview[]>([]);
  const [view, setView] = useState<View>("welcome");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState("");
  const [streamedAnswer, setStreamedAnswer] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [sidebar, setSidebar] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<CaseFile | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [pinned, setPinned] = useState<string[]>([]);
  const [simulation, setSimulation] = useState<Simulation | null>(null);
  const [consentRequest, setConsentRequest] = useState<ConsentRequest | null>(null);
  const [activeConsent, setActiveConsent] = useState<ModelConsent | null>(null);

  useEffect(() => {
    setPinned(JSON.parse(localStorage.getItem("pinned-legal-cases") ?? "[]"));
    void loadCases();
  }, []);

  async function loadCases(selectId?: string) {
    setLoading(true);
    try {
      const list = await api.listCases();
      setCases(list);
      const saved = selectId ?? localStorage.getItem("legal-case-id");
      const selected = list.find((item) => item.id === saved) ?? null;
      setCurrent(selected);
      if (selected) {
        const hasCurrentReport = selected.analyses.some((item) => item.is_current);
        setView(hasCurrentReport ? "report" : "chat");
        setReviews(await api.listReviews(selected.id));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "暂时无法连接服务，请稍后重试");
    } finally {
      setLoading(false);
    }
  }

  async function createCase(starter?: string) {
    setBusy("正在创建你的保密案件空间");
    setError("");
    try {
      const item = await api.createCase();
      localStorage.setItem("legal-case-id", item.id);
      setCurrent(item);
      setCases((previous) => [item, ...previous]);
      setReviews([]);
      setView("chat");
      if (starter) setInput(starter);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建咨询失败");
    } finally {
      setBusy("");
    }
  }

  async function selectCase(item: CaseFile) {
    localStorage.setItem("legal-case-id", item.id);
    setCurrent(item);
    setReviews(await api.listReviews(item.id).catch(() => []));
    setView(item.analyses.some((analysis) => analysis.is_current) ? "report" : "chat");
    setSidebar(false);
    setSimulation(null);
  }

  async function refresh(id = current?.id) {
    if (!id) return;
    const [updated, nextReviews] = await Promise.all([api.getCase(id), api.listReviews(id)]);
    setCurrent(updated);
    setReviews(nextReviews);
    setCases((items) => [updated, ...items.filter((item) => item.id !== id)]);
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!current || !input.trim() || busy) return;
    const content = input.trim();
    if (!await ensureModelConsent("intake", content)) return;
    await sendWithModelConsent(content);
  }

  async function sendWithModelConsent(content: string) {
    if (!current) return;
    setInput("");
    setBusy("正在理解你的情况");
    setStreamedAnswer("");
    setError("");
    const optimistic = { id: `pending-${Date.now()}`, role: "user", content, created_at: new Date().toISOString() };
    setCurrent({ ...current, messages: [...current.messages, optimistic] });
    try {
      await api.streamMessage(current.id, content, {
        onStatus: setBusy,
        onToken: (token) => setStreamedAnswer((answer) => answer + token),
      });
      await refresh(current.id);
      setStreamedAnswer("");
    } catch (cause) {
      setInput(content);
      setError(cause instanceof Error ? cause.message : "消息发送失败，内容已为你保留");
    } finally {
      setBusy("");
    }
  }

  async function analyze() {
    if (!current || busy) return;
    if (!await ensureModelConsent("analysis")) return;
    await analyzeWithModelConsent();
  }

  async function analyzeWithModelConsent() {
    if (!current) return;
    setBusy("正在从双方立场核对事实、证据和法律依据");
    setError("");
    try {
      await api.analyze(current.id);
      await refresh(current.id);
      setView("report");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "分析暂时失败");
    } finally {
      setBusy("");
    }
  }

  async function reviewFact(fact: Fact, status: "confirmed" | "disputed") {
    if (!current) return;
    setBusy(status === "confirmed" ? "正在确认事实" : "正在标记争议");
    try {
      await api.updateFact(current.id, fact.id, { status, occurred_on: fact.occurred_on });
      await refresh(current.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "事实状态更新失败");
    } finally {
      setBusy("");
    }
  }

  async function simulate() {
    if (!current || busy) return;
    if (!await ensureModelConsent("simulation")) return;
    await simulateWithModelConsent();
  }

  async function simulateWithModelConsent() {
    if (!current) return;
    setBusy("正在恢复仲裁模拟环境");
    setError("");
    try {
      setSimulation(await api.simulate(current.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "模拟暂时无法启动");
    } finally {
      setBusy("");
    }
  }

  async function ensureModelConsent(purpose: ConsentPurpose, content?: string) {
    if (!current) return false;
    setBusy("正在检查模型数据授权");
    setError("");
    try {
      const consents = await api.listModelConsents(current.id);
      const active = consents.find((item) => item.status === "active" && !item.revoked_at) ?? null;
      if (!active?.purposes.includes(purpose)) {
        setActiveConsent(active);
        setConsentRequest({ purpose, content });
        return false;
      }
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法检查模型授权");
      return false;
    } finally {
      setBusy("");
    }
  }

  async function grantRequestedConsent() {
    if (!current || !consentRequest || busy) return;
    const request = consentRequest;
    setBusy("正在记录案件模型授权");
    setError("");
    try {
      await api.grantModelConsent(
        current.id,
        mergeConsentScope(activeConsent, request.purpose),
      );
      setConsentRequest(null);
      setActiveConsent(null);
      if (request.purpose === "intake" && request.content) await sendWithModelConsent(request.content);
      if (request.purpose === "analysis") await analyzeWithModelConsent();
      if (request.purpose === "simulation") await simulateWithModelConsent();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "模型授权失败");
    } finally {
      setBusy("");
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setBusy("正在永久删除案件数据");
    try {
      await api.deleteCase(deleteTarget.id);
      const remaining = cases.filter((item) => item.id !== deleteTarget.id);
      setCases(remaining);
      setDeleteTarget(null);
      if (current?.id === deleteTarget.id) {
        localStorage.removeItem("legal-case-id");
        setCurrent(null);
        setReviews([]);
        setView("welcome");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除失败");
    } finally {
      setBusy("");
    }
  }

  function togglePin(item: CaseFile) {
    const next = pinned.includes(item.id) ? pinned.filter((id) => id !== item.id) : [item.id, ...pinned];
    setPinned(next);
    localStorage.setItem("pinned-legal-cases", JSON.stringify(next));
  }

  const sortedCases = useMemo(
    () => [...cases].sort((a, b) => Number(pinned.includes(b.id)) - Number(pinned.includes(a.id))),
    [cases, pinned],
  );

  if (loading) return <LoadingScreen />;

  return (
    <main className="app-shell">
      <Sidebar
        open={sidebar}
        cases={sortedCases}
        pinned={pinned}
        current={current}
        onClose={() => setSidebar(false)}
        onNew={() => void createCase()}
        onSelect={(item) => void selectCase(item)}
        onPin={togglePin}
        onDelete={setDeleteTarget}
      />
      <section className="main-stage">
        <Header
          current={current}
          onMenu={() => setSidebar(true)}
          onHome={() => {
            setView("welcome");
            setCurrent(null);
            setReviews([]);
            localStorage.removeItem("legal-case-id");
          }}
        />
        {error && <ErrorToast message={error} onClose={() => setError("")} />}
        {view === "welcome" && <Welcome busy={busy} onStart={(starter) => void createCase(starter)} />}
        {view === "chat" && current && (
          <ChatWorkspace
            caseFile={current}
            reviews={reviews}
            input={input}
            setInput={setInput}
            busy={busy}
            streamedAnswer={streamedAnswer}
            onSend={send}
            onAnalyze={() => void analyze()}
            onEvidence={() => setEvidenceOpen(true)}
            onReport={() => setView("report")}
            onReviewFact={(fact, status) => void reviewFact(fact, status)}
          />
        )}
        {view === "report" && current && (
          <Report
            caseFile={current}
            reviews={reviews}
            onBack={() => setView("chat")}
            onAnalyze={() => void analyze()}
            onSimulate={() => void simulate()}
            busy={busy}
          />
        )}
      </section>
      {deleteTarget && <DeleteDialog item={deleteTarget} busy={busy} onCancel={() => setDeleteTarget(null)} onConfirm={() => void confirmDelete()} />}
      {evidenceOpen && current && <EvidenceDialog caseId={current.id} onClose={() => setEvidenceOpen(false)} onSaved={async () => { setEvidenceOpen(false); await refresh(); }} />}
      {consentRequest && <ModelConsentDialog purpose={consentRequest.purpose} busy={busy} onCancel={() => { setConsentRequest(null); setActiveConsent(null); }} onConfirm={() => void grantRequestedConsent()} />}
      {simulation && <SimulationOverlay session={simulation} onClose={() => setSimulation(null)} />}
    </main>
  );
}

function LoadingScreen() {
  return <main className="loading-screen" aria-live="polite"><span className="brand-mark">衡</span><div><i /><i /><i /></div><p>正在进入法衡工作台</p></main>;
}

function ErrorToast({ message, onClose }: { message: string; onClose: () => void }) {
  return <div className="toast error-toast" role="alert"><span className="toast-icon">!</span><div><b>操作未完成</b><p>{message}</p></div><button onClick={onClose} aria-label="关闭错误提示">×</button></div>;
}

function Sidebar({ open, cases, pinned, current, onClose, onNew, onSelect, onPin, onDelete }: {
  open: boolean; cases: CaseFile[]; pinned: string[]; current: CaseFile | null;
  onClose: () => void; onNew: () => void; onSelect: (item: CaseFile) => void;
  onPin: (item: CaseFile) => void; onDelete: (item: CaseFile) => void;
}) {
  return <>
    <aside className={`sidebar ${open ? "open" : ""}`} aria-label="案件导航">
      <div className="logo"><span>衡</span><div><b>法衡</b><small>劳动争议决策助手</small></div></div>
      <button className="new-case" onClick={onNew}><i>＋</i><span>新建保密咨询</span></button>
      <div className="history-head"><span>我的咨询</span><em>{cases.length}</em></div>
      <div className="case-list">
        {cases.length ? cases.map((item) => (
          <article className={`case-row ${current?.id === item.id ? "selected" : ""}`} key={item.id}>
            <button className="case-select" onClick={() => onSelect(item)} aria-current={current?.id === item.id ? "page" : undefined}>
              <i>{pinned.includes(item.id) ? "置" : "案"}</i>
              <span><b>{item.title}</b><small>{stageLabels[item.stage] ?? "案件处理中"} · {formatDate(item.updated_at)}</small></span>
            </button>
            <div className="row-actions">
              <button aria-label={pinned.includes(item.id) ? "取消置顶" : "置顶案件"} title={pinned.includes(item.id) ? "取消置顶" : "置顶案件"} onClick={() => onPin(item)}>⌁</button>
              <button aria-label="删除案件" title="删除案件" onClick={() => onDelete(item)}>×</button>
            </div>
          </article>
        )) : <div className="no-history"><span>◇</span><b>还没有咨询记录</b><small>新建咨询后，案件会显示在这里</small></div>}
      </div>
      <div className="sidebar-foot"><span className="secure-dot" /><div><b>隐私保护已开启</b><small>事实与推断分级记录</small></div></div>
    </aside>
    {open && <button className="backdrop" onClick={onClose} aria-label="关闭案件导航" />}
  </>;
}

function Header({ current, onMenu, onHome }: { current: CaseFile | null; onMenu: () => void; onHome: () => void }) {
  return <header className="app-header">
    <button className="menu-button" onClick={onMenu} aria-label="打开案件导航">☰</button>
    <button className="crumb" onClick={onHome}>工作台</button>
    {current && <><span className="slash">/</span><span className="current-title">{current.title}</span><span className={`stage-pill ${current.stage}`}>{stageLabels[current.stage] ?? "处理中"}</span></>}
    <div className="header-trust"><i />保密会话</div>
  </header>;
}

function Welcome({ busy, onStart }: { busy: string; onStart: (starter?: string) => void }) {
  return <div className="welcome-page">
    <section className="hero">
      <div className="hero-kicker"><span />证据驱动的劳动法律助手</div>
      <h1>把复杂争议，<br /><em>梳理成清晰的下一步。</em></h1>
      <p>从事实确认、证据审查到法律检索与双方推演，全程保留依据，不把推测当作事实。</p>
      <button className="hero-action" disabled={!!busy} onClick={() => onStart()}>{busy || "开始保密咨询"}<span>→</span></button>
      <div className="trust-row"><span><i>✓</i> 事实分级</span><span><i>✓</i> 引用可核验</span><span><i>✓</i> 高风险转人工</span></div>
    </section>
    <section className="scenario-section">
      <div className="section-title"><div><small>快速开始</small><h2>你遇到了什么问题？</h2></div><p>选择相近场景，或直接开始后自由描述</p></div>
      <div className="starter-grid">{starters.map((item) => <button key={item.title} onClick={() => onStart(item.text)}><i>{item.icon}</i><span><b>{item.title}</b><small>{item.detail}</small></span><em>→</em></button>)}</div>
    </section>
    <footer className="welcome-foot"><b>重要提示</b> 本产品提供法律信息与决策辅助，不构成律师意见，不承诺案件结果。</footer>
  </div>;
}

function ChatWorkspace({ caseFile, reviews, input, setInput, busy, streamedAnswer, onSend, onAnalyze, onEvidence, onReport, onReviewFact }: {
  caseFile: CaseFile; reviews: HumanReview[]; input: string; setInput: (value: string) => void; busy: string; streamedAnswer: string;
  onSend: (event: FormEvent) => void; onAnalyze: () => void; onEvidence: () => void; onReport: () => void;
  onReviewFact: (fact: Fact, status: "confirmed" | "disputed") => void;
}) {
  const currentAnalysis = caseFile.analyses.findLast((item) => item.is_current);
  const pendingReview = reviews.find((item) => item.status === "pending");
  const reliableFacts = caseFile.facts.filter((fact) => ["confirmed", "evidence_supported"].includes(fact.status)).length;
  const readiness = Math.min(100, 12 + reliableFacts * 12 + caseFile.evidence.length * 16 + Math.min(caseFile.messages.length, 4) * 4);
  const streamEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => { streamEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [caseFile.messages.length, busy, streamedAnswer]);

  return <div className="desk">
    <section className="chat-column">
      <div className="chat-head"><div><span className="agent-orb">衡</span><div><h2>案件协调助手</h2><p><i />正在维护事实、证据与法律依据</p></div></div><button onClick={currentAnalysis ? onReport : onAnalyze}>{currentAnalysis ? "查看分析报告" : "生成案件分析"}</button></div>
      {pendingReview && <div className="review-banner"><span>审</span><div><b>本案已进入专业复核</b><p>系统发现需要人工判断的风险点。当前报告仅供参考，复核完成前不会作为确定结论。</p></div></div>}
      <div className="message-stream" aria-live="polite">
        {!caseFile.messages.length && <div className="assistant-card intro"><span className="mini-orb">衡</span><div><b>你好，我们先从发生了什么开始。</b><p>请尽量按时间顺序描述。不需要使用法律术语；日期、工资、通知方式和手头材料会帮助我更准确地梳理。</p><div className="quick-prompts">{starters.slice(0, 3).map((item) => <button key={item.title} onClick={() => setInput(item.text)}>{item.title}</button>)}</div></div></div>}
        {caseFile.messages.map((message) => <div key={message.id} className={`bubble-row ${message.role}`}><span>{message.role === "user" ? "我" : "衡"}</span><div><small>{message.role === "user" ? "你的陈述" : "案件协调助手"}</small><p>{message.role === "assistant" ? normalizeConsultationContent(message.content) : message.content}</p></div></div>)}
        {streamedAnswer && <div className="bubble-row assistant streaming"><span>衡</span><div><small>案件协调助手 · 正在回答</small><p>{normalizeConsultationContent(streamedAnswer)}<i className="stream-cursor" /></p></div></div>}
        {busy && !streamedAnswer && <div className="thinking"><span className="mini-orb">衡</span><div><b>{busy}</b><p><i /><i /><i /></p></div></div>}
        <div ref={streamEndRef} />
      </div>
      <form className="premium-composer" onSubmit={onSend}>
        <label className="sr-only" htmlFor="case-message">描述案件情况</label>
        <textarea id="case-message" value={input} maxLength={10000} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="描述新情况，或回答助手的问题……" />
        <div><button type="button" onClick={onEvidence}>＋ 登记证据</button><span>{input.length ? `${input.length.toLocaleString()} / 10,000` : "Enter 发送 · Shift + Enter 换行"}</span><button className="send-button" disabled={!!busy || !input.trim()}>{busy ? "处理中" : "发送"}<i>↑</i></button></div>
      </form>
    </section>
    <aside className="case-insight">
      <div className="insight-head"><div><small>CASE FILE</small><span>案件档案</span></div><b>{stageLabels[caseFile.stage] ?? "处理中"}</b></div>
      <StageProgress stage={caseFile.stage} />
      <section className="readiness"><div><span>分析准备度</span><b>{readiness}%</b></div><div className="meter"><i style={{ width: `${readiness}%` }} /></div><p>{readiness < 55 ? "先确认关键事实并登记证据，能够显著提高分析可靠性。" : "已具备初步分析条件，仍需关注未确认事实。"}</p></section>
      <InsightSection title="事实记录" count={caseFile.facts.length}>{caseFile.facts.slice(-5).map((fact) => <article className="fact-item" key={fact.id}><i className={fact.status} /><span>{fact.content}<small>{factLabels[fact.status] ?? fact.status}</small>{fact.status === "user_stated" && <em><button disabled={!!busy} onClick={() => onReviewFact(fact, "confirmed")}>确认</button><button disabled={!!busy} onClick={() => onReviewFact(fact, "disputed")}>有争议</button></em>}</span></article>)}</InsightSection>
      <InsightSection title="证据材料" count={caseFile.evidence.length}>{caseFile.evidence.map((item) => <article className="evidence-item" key={item.id}><i /><span>{item.name}<small>{item.purpose}</small></span></article>)}{!caseFile.evidence.length && <button className="add-evidence" onClick={onEvidence}>＋ 登记第一份证据</button>}</InsightSection>
      <div className="privacy-card"><span>盾</span><div><b>事实边界保护</b><p>用户陈述、证据支持与模型推断分别记录，材料变化后旧分析会自动失效。</p></div></div>
    </aside>
  </div>;
}

function StageProgress({ stage }: { stage: string }) {
  const stages = ["fact_review", "evidence_review", "issue_identification", "strategy_ready"];
  const currentIndex = stage === "human_review" ? 3 : Math.max(0, stages.indexOf(stage));
  return <div className="stage-progress" aria-label="案件进度">{["事实", "证据", "争点", "分析"].map((label, index) => <span className={index <= currentIndex ? "done" : ""} key={label}><i>{index < currentIndex ? "✓" : index + 1}</i><small>{label}</small></span>)}</div>;
}

function InsightSection({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return <section className="insight-section"><header><b>{title}</b><span>{count}</span></header><div>{children || <p className="muted">暂无记录</p>}</div></section>;
}

function Report({ caseFile, reviews, onBack, onAnalyze, onSimulate, busy }: { caseFile: CaseFile; reviews: HumanReview[]; onBack: () => void; onAnalyze: () => void; onSimulate: () => void; busy: string }) {
  const result = caseFile.analyses.findLast((item) => item.is_current);
  const stale = result ? undefined : caseFile.analyses.at(-1);
  const pendingReview = reviews.find((item) => item.status === "pending" && item.analysis_id === result?.id);
  return <div className="report-page">
    <div className="report-top"><button onClick={onBack}>← 返回咨询</button><div><span>TRACEABLE CASE MEMO</span><h1>案件策略分析</h1><p>基于当前事实、证据与有效法律依据 · {formatDate(caseFile.updated_at)}</p></div><button className="outline-button" onClick={onAnalyze} disabled={!!busy}>{busy || "重新分析"}</button></div>
    {result ? <>
      {pendingReview && <section className="report-review-alert"><span>专业复核中</span><div><b>以下分析存在需要人工确认的风险点</b><ul>{pendingReview.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></div></section>}
      <section className="report-summary"><div><span>证据成熟度</span><h2>{Math.round(result.confidence * 100)}<small>%</small></h2><p>不是胜诉概率</p></div><div><small>中立评估摘要</small><p>{extractNeutral(result.viewpoint)}</p></div></section>
      <div className="report-grid">
        <ReportCard tone="favorable" icon="申" eyebrow="劳动者代理" title="可主张方向"><p>{result.viewpoint.split("中立评估：")[0]}</p></ReportCard>
        <ReportCard tone="adverse" icon="辩" eyebrow="用人单位代理" title="重点抗辩"><p>{result.counterargument}</p></ReportCard>
        <ReportCard tone="neutral wide" icon="审" eyebrow="中立审查" title="不确定性与风险"><ul>{result.uncertainties.map((item) => <li key={item}>{item}</li>)}</ul></ReportCard>
      </div>
      {result.reasoning_trace?.length > 0 && <section className="reasoning-section"><header><div><small>EVIDENCE CHAIN</small><h2>争点与构成要件</h2></div><p>每项判断均关联案件事实、证据与法律依据</p></header><div>{result.reasoning_trace.map((trace) => <article key={trace.issue}><div><span>争议焦点</span><h3>{trace.issue}</h3><small>{trace.authority_ids.length} 条法律依据</small></div><ul>{trace.elements.map((element) => <li key={element.element}><i className={element.status}>{element.status === "supported" ? "✓" : element.status === "claimed" ? "·" : "!"}</i><span><b>{element.element}</b><small>{element.status === "supported" ? "已有证据支持" : element.status === "claimed" ? "仅有事实陈述" : "尚缺必要信息"}</small></span></li>)}</ul></article>)}</div></section>}
      <section className="next-actions"><div><small>NEXT STEP</small><h2>用模拟庭审检验表达与证据</h2><p>模拟内容不会写入已确认案件事实。</p></div><button onClick={onSimulate}>进入仲裁模拟 <span>→</span></button></section>
      <p className="report-disclaimer">本报告仅提供法律信息和决策辅助，不构成律师意见，不承诺案件结果。</p>
    </> : <div className="empty-report"><span>{stale ? "↻" : "◇"}</span><h2>{stale ? "案件材料已变化，需要重新分析" : "还没有分析报告"}</h2><p>{stale?.invalidated_reason ?? "完成事实陈述并登记关键证据后，可以从双方立场生成案件分析。"}</p><button onClick={onAnalyze} disabled={!!busy}>{busy || "生成最新分析"}</button></div>}
  </div>;
}

function ReportCard({ tone, icon, eyebrow, title, children }: { tone: string; icon: string; eyebrow: string; title: string; children: ReactNode }) {
  return <section className={`report-card ${tone}`}><header><i>{icon}</i><div><span>{eyebrow}</span><b>{title}</b></div></header>{children}</section>;
}

function ModelConsentDialog({ purpose, busy, onCancel, onConfirm }: { purpose: ConsentPurpose; busy: string; onCancel: () => void; onConfirm: () => void }) {
  const copy = {
    intake: { title: "授权案件协调助手", purpose: "理解当前问题、整理案件事实并生成咨询回应", data: "本轮对话、案件事实、证据名称与证明目的", action: "同意并发送消息" },
    analysis: { title: "授权多方视角案件分析", purpose: "分别整理劳动者观点、单位可能的答辩和中立评估", data: "案件事实、证据名称、证明目的和已有分析", action: "同意并开始分析" },
    simulation: { title: "授权用于仲裁模拟", purpose: "生成仲裁员和用人单位代理人的模拟回应", data: "本轮模拟对话、案件事实、证据名称与证明目的", action: "同意并开始模拟" },
  }[purpose];
  return <div className="modal-layer" role="presentation"><div className="dialog consent-dialog" role="dialog" aria-modal="true" aria-labelledby="consent-title"><div className="consent-scroll"><div className="consent-mark">隐</div><span className="form-kicker">信息使用授权</span><h2 id="consent-title">{copy.title}</h2><p>该功能需要把以下案件信息发送给 DeepSeek。只有你确认后才会发送。</p><ul><li><b>用来做什么</b><span>{copy.purpose}</span></li><li><b>会使用什么</b><span>{copy.data}</span></li><li><b>如何保护</b><span>发送前会隐藏身份证号、手机号等敏感信息；不会改动你保存的原始材料</span></li><li><b>你可以撤销</b><span>授权只对当前案件生效，以后可以撤销；新增用途不会取消已有授权</span></li></ul><div className="consent-warning">生成内容只用于案件辅助，不是律师意见，也不会自动变成已经确认的事实。</div></div><div className="dialog-actions"><button disabled={Boolean(busy)} onClick={onCancel}>暂不授权</button><button className="primary-button" disabled={Boolean(busy)} onClick={onConfirm}>{busy || copy.action}</button></div></div></div>;
}

function DeleteDialog({ item, busy, onCancel, onConfirm }: { item: CaseFile; busy: string; onCancel: () => void; onConfirm: () => void }) {
  return <div className="modal-layer" role="presentation"><div className="dialog delete-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title"><span className="danger-icon">!</span><h2 id="delete-title">永久删除这次咨询？</h2><p>“{item.title}”的会话、事实、证据、分析、模拟和文书将被永久删除，且无法恢复。</p><div className="dialog-actions"><button onClick={onCancel}>保留咨询</button><button className="danger-button" disabled={!!busy} onClick={onConfirm}>{busy || "确认永久删除"}</button></div></div></div>;
}

function EvidenceDialog({ caseId, onClose, onSaved }: { caseId: string; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [saving, setSaving] = useState(false);
  async function save(event: FormEvent) { event.preventDefault(); setSaving(true); try { await api.addEvidence(caseId, { name: name.trim(), evidence_type: "document", purpose: purpose.trim() }); await onSaved(); } finally { setSaving(false); } }
  return <div className="modal-layer"><form className="dialog evidence-dialog" onSubmit={save} role="dialog" aria-modal="true" aria-labelledby="evidence-title"><span className="form-kicker">EVIDENCE RECORD</span><h2 id="evidence-title">登记证据材料</h2><p>当前记录材料名称与证明目的，不上传文件正文。请避免填写无关敏感信息。</p><label>证据名称<input autoFocus value={name} maxLength={200} onChange={(event) => setName(event.target.value)} placeholder="例如：解除劳动合同通知书" required /></label><label>这份材料能够证明什么？<textarea value={purpose} maxLength={1000} onChange={(event) => setPurpose(event.target.value)} placeholder="例如：证明公司于6月1日单方解除劳动合同" required /></label><div className="dialog-actions"><button type="button" onClick={onClose}>取消</button><button className="primary-button" disabled={saving || !name.trim() || !purpose.trim()}>{saving ? "保存中…" : "保存证据"}</button></div></form></div>;
}

function SimulationOverlay({ session, onClose }: { session: Simulation; onClose: () => void }) {
  const [current, setCurrent] = useState(session);
  const [answer, setAnswer] = useState("");
  const [sending, setSending] = useState(false);
  const [ending, setEnding] = useState(false);
  const [failure, setFailure] = useState("");
  const [mobilePanel, setMobilePanel] = useState<"hearing" | "coach">("hearing");
  const streamRef = useRef<HTMLDivElement>(null);
  useEffect(() => { streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: "smooth" }); }, [current.transcript.length]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!answer.trim()) return;
    const content = answer.trim();
    const base = current.transcript;
    setAnswer(""); setSending(true); setFailure("");
    setCurrent({ ...current, transcript: [...base, { role: "劳动者（你）", agent_id: "worker", content }] });
    try { setCurrent(await api.simulationMessage(current.id, content)); }
    catch { setAnswer(content); setCurrent({ ...current, transcript: base }); setFailure("回应生成失败，内容已保留，请重试。"); }
    finally { setSending(false); }
  }
  async function complete() {
    if (ending || sending) return;
    setEnding(true); setFailure("");
    try { await api.completeSimulation(current.id); onClose(); }
    catch { setFailure("暂时无法结束本次模拟，请稍后重试。当前对话仍然保留。"); }
    finally { setEnding(false); }
  }
  const runtime = current.transcript.find((line) => line.agent_id === "system");
  const mode = runtime?.mode ?? "rule";
  const modeLabels = { model: "智能模拟", hybrid: "部分智能回应", rule: "基础模拟" };
  const stageLabels: Record<string, string> = { orientation: "身份与程序确认", claims: "仲裁请求", fact_investigation: "事实调查", evidence_examination: "举证质证", debate: "辩论", closing_or_mediation: "最后陈述／调解" };
  const executionLabels: Record<string, string> = { employer_advocate: "单位代表回应", arbitrator: "仲裁员整理问题", worker_coach: "仲裁助手给出建议" };
  const visibleTranscript = current.transcript.filter((line) => line.agent_id !== "system");
  return <div className="simulation-overlay" role="dialog" aria-modal="true" aria-label="劳动仲裁庭审模拟"><header><div><span>法衡 · 模拟仲裁庭</span><b>劳动仲裁模拟</b></div><div className={`simulation-status ${mode}`}><i />{modeLabels[mode]}</div><button onClick={onClose}>退出</button></header><nav className="simulation-mobile-tabs" aria-label="切换模拟内容"><button className={mobilePanel === "hearing" ? "active" : ""} onClick={() => setMobilePanel("hearing")}>庭审对话</button><button className={mobilePanel === "coach" ? "active" : ""} onClick={() => setMobilePanel("coach")}>仲裁建议</button></nav><main><section className={`hearing-room ${mobilePanel === "hearing" ? "mobile-active" : ""}`}><div className="bench"><span>{stageLabels[runtime?.stage ?? "orientation"]} · 第 {runtime?.round_number ?? 0} 回合</span><b><i>⚖️</i> 中立仲裁员</b></div><div className="role-legend" aria-label="模拟角色说明"><span className="arbitrator"><i>⚖️</i>仲裁员</span><span className="employer"><i>🏢</i>单位代表</span><span className="coach"><i>🧭</i>仲裁助手</span><span className="worker"><i>👤</i>你</span></div>{Boolean(runtime?.last_execution?.length) && <div className="round-trace" aria-label="本轮回应过程">{runtime?.last_execution?.map((agent, index) => <span className={runtime.fallback_agents?.includes(agent) ? "fallback" : ""} key={`${agent}-${index}`}>{index + 1}<b>{executionLabels[agent] ?? agent}</b>{runtime.fallback_agents?.includes(agent) && <small>已使用备用回答</small>}</span>)}</div>}<div className="hearing-stream" ref={streamRef} aria-live="polite">{visibleTranscript.map((line, index) => { const identity = simulationIdentity(line); return <article className={`${identity.id}${line.kind === "question" ? " question" : ""}`} key={`${line.role}-${index}`}><div className="avatar-stack"><small>{identity.label}</small><span aria-hidden="true"><em>{identity.icon}</em><Image src={identity.avatar} alt="" width={56} height={56} onError={(event) => { event.currentTarget.style.display = "none"; }} /></span></div><div className="message-card"><b>{identity.tag}</b><p>{line.content}</p></div></article>; })}{sending && <div className="hearing-thinking"><i /><i /><i /><span>正在准备单位回应和仲裁员提问…</span></div>}</div>{failure && <p className="inline-error" role="alert">{failure}</p>}<form className="hearing-composer" onSubmit={submit}><label className="sr-only" htmlFor="hearing-answer">输入你的回答</label><div><textarea id="hearing-answer" value={answer} onChange={(event) => setAnswer(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="输入你的回答，也可以询问当前流程…" /><small>Enter 发送 · Shift + Enter 换行</small></div><button disabled={sending || !answer.trim()}>{sending ? "提交中…" : "提交回答"}</button></form></section><aside className={`coach-panel ${mobilePanel === "coach" ? "mobile-active" : ""}`}><div className="coach-identity"><i><em>🧭</em><Image src="/avatars/coach.png" alt="" width={56} height={56} onError={(event) => { event.currentTarget.style.display = "none"; }} /></i><div><span className="form-kicker">仲裁助手</span><h2>怎样回答更清楚</h2></div></div><p>这里会提示表达和证据上的改进方向，不会代替你发言，也不会把练习内容当成已经确认的事实。</p><ul>{current.feedback.map((item) => <li key={item}>{item}</li>)}</ul><button disabled={ending || sending} onClick={() => void complete()}>{ending ? "正在结束…" : "结束本次模拟"}</button></aside></main></div>;
}

function simulationIdentity(line: SimulationLine) {
  if (line.agent_id === "system" || line.role === "系统") return { id: "system", icon: "💡", avatar: "/avatars/coach.png", label: "模拟提示", tag: "当前状态" };
  if (line.agent_id === "employer_advocate" || line.role.includes("用人单位")) return { id: "employer", icon: "🏢", avatar: "/avatars/employer.png", label: "单位代表", tag: "单位一方" };
  if (line.agent_id === "worker" || line.role.includes("劳动者")) return { id: "worker", icon: "👤", avatar: "/avatars/worker.png", label: "你", tag: "你的发言" };
  return { id: "arbitrator", icon: "⚖️", avatar: "/avatars/arbitrator.png", label: "仲裁员", tag: "中立主持" };
}

function extractNeutral(viewpoint: string) {
  return viewpoint.split("中立评估：")[1]?.split("可能结果：")[0]?.trim() || viewpoint;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(new Date(value));
}
