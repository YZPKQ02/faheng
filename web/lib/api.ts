export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Message = { id: string; role: string; content: string; agent?: string; created_at: string };
export type Fact = { id: string; content: string; status: string; source: string; occurred_on?: string | null };
export type Evidence = { id: string; name: string; evidence_type: string; purpose: string; authenticity: string };
export type ReasoningElement = { element: string; status: string; fact_ids: string[]; evidence_ids: string[] };
export type ReasoningTrace = { issue: string; as_of: string; elements: ReasoningElement[]; authority_ids: string[] };
export type Conclusion = { id: string; viewpoint: string; counterargument: string; confidence: number; uncertainties: string[]; authority_ids: string[]; reasoning_trace: ReasoningTrace[]; quality_metrics: Record<string, unknown>; is_current: boolean; invalidated_reason?: string | null };
export type CaseFile = { id: string; title: string; stage: string; goal: string; risk_level: string; created_at: string; updated_at: string; facts: Fact[]; evidence: Evidence[]; messages: Message[]; analyses: Conclusion[] };
export type HumanReview = { id: string; case_id: string; analysis_id: string; risk_level: string; reasons: string[]; status: string; decision?: string | null; reviewer?: string | null; notes?: string | null; created_at: string; reviewed_at?: string | null };
export type ModelConsent = { id: string; case_id: string; provider: string; purposes: string[]; data_categories: string[]; status: string; version: number; granted_at: string; revoked_at?: string | null };
export type SimulationLine = { role: string; content: string; agent_id?: "system" | "arbitrator" | "employer_advocate" | "worker"; mode?: "model" | "rule" | "hybrid"; mode_reason?: string | null; stage?: string; round_number?: number; last_execution?: string[]; fallback_agents?: string[]; kind?: "question"; stream_id?: string };
export type Simulation = { id: string; scenario: string; user_role: string; transcript: SimulationLine[]; feedback: string[]; suggested_answers: string[]; assistance_mode: "coach"; counsel_agent_id: string; counsel_memory_version: number; counsel_memory_snapshot: Record<string, unknown>; status: "active" | "completed"; created_at: string; updated_at: string };

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(payload.detail ?? "请求失败");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  createCase: () => request<CaseFile>("/cases", { method: "POST", body: JSON.stringify({}) }),
  listCases: () => request<CaseFile[]>("/cases"),
  getCase: (id: string) => request<CaseFile>(`/cases/${id}`),
  deleteCase: (id: string) => request<void>(`/cases/${id}`, { method: "DELETE" }),
  sendMessage: (id: string, content: string) => request<{ message: Message; missing_information: string[] }>(`/cases/${id}/messages`, { method: "POST", body: JSON.stringify({ content }) }),
  streamMessage: async (
    id: string,
    content: string,
    handlers: { onStatus: (label: string) => void; onToken: (token: string) => void },
  ) => {
    const response = await fetch(`${API_URL}/cases/${id}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ content }),
    });
    if (!response.ok || !response.body) throw new Error("无法建立流式会话");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const event = frame.match(/^event: (.+)$/m)?.[1];
        const raw = frame.match(/^data: (.+)$/m)?.[1];
        if (!event || !raw) continue;
        const payload = JSON.parse(raw);
        if (event === "status") handlers.onStatus(payload.label);
        if (event === "token") handlers.onToken(payload.content);
        if (event === "error") throw new Error(payload.message);
      }
      if (done) break;
    }
  },
  addEvidence: (id: string, payload: { name: string; evidence_type: string; purpose: string }) => request<Evidence>(`/cases/${id}/evidence`, { method: "POST", body: JSON.stringify(payload) }),
  updateFact: (caseId: string, factId: string, payload: { status: string; occurred_on?: string | null }) => request<Fact>(`/cases/${caseId}/facts/${factId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  analyze: (id: string) => request<{ conclusions: Conclusion[]; evidence_gaps: string[]; next_steps: string[]; disclaimer: string; requires_human_review: boolean; blocked_reasons: string[] }>(`/cases/${id}/analysis`, { method: "POST", body: JSON.stringify({}) }),
  listReviews: (id: string) => request<HumanReview[]>(`/cases/${id}/reviews`),
  listModelConsents: (id: string) => request<ModelConsent[]>(`/cases/${id}/model-consents`),
  grantModelConsent: (id: string, payload: { purposes: string[]; data_categories: string[] }) => request<ModelConsent>(`/cases/${id}/model-consents`, { method: "POST", body: JSON.stringify({ provider: "deepseek", ...payload }) }),
  simulate: (id: string) => request<Simulation>(`/cases/${id}/simulations/active`, { method: "PUT", body: JSON.stringify({ scenario: "arbitration", user_role: "worker" }) }),
  simulationMessage: (id: string, content: string) => request<Simulation>(`/simulations/${id}/messages`, { method: "POST", body: JSON.stringify({ content }) }),
  streamSimulationMessage: async (
    id: string,
    content: string,
    handlers: {
      onStatus: (label: string) => void;
      onAgentStart: (line: SimulationLine) => void;
      onAgentToken: (streamId: string, token: string) => void;
      onAgentComplete: (line: SimulationLine) => void;
      onCounsel: (feedback: string[], suggestedAnswers: string[]) => void;
      onComplete: (session: Simulation) => void;
    },
  ) => {
    const response = await fetch(`${API_URL}/simulations/${id}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ content }),
    });
    if (!response.ok || !response.body) throw new Error("无法建立仲裁模拟流");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const event = frame.match(/^event: (.+)$/m)?.[1];
        const raw = frame.match(/^data: (.+)$/m)?.[1];
        if (!event || !raw) continue;
        const payload = JSON.parse(raw);
        if (event === "status") handlers.onStatus(payload.label);
        if (event === "agent_start") handlers.onAgentStart(payload);
        if (event === "agent_token") handlers.onAgentToken(payload.stream_id, payload.content);
        if (event === "agent_complete") handlers.onAgentComplete(payload);
        if (event === "counsel") handlers.onCounsel(payload.feedback, payload.suggested_answers);
        if (event === "complete") handlers.onComplete(payload.session);
        if (event === "error") throw new Error(payload.message);
      }
      if (done) break;
    }
  },
  completeSimulation: (id: string) => request<Simulation>(`/simulations/${id}/complete`, { method: "POST" }),
};
