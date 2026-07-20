import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../lib/api";
import { normalizeConsultationContent } from "../lib/format";
import { mergeConsentScope } from "../lib/consent";

describe("legal advisor workspace", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the legal-risk disclaimer in the product copy", () => {
    const disclaimer = "本产品提供法律信息与案件梳理，不替代执业律师，不承诺胜诉";
    expect(disclaimer).toContain("不承诺胜诉");
  });

  it("accepts a successful 204 response when deleting a conversation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.deleteCase("case-1")).resolves.toBeUndefined();
  });

  it("sends a structured fact review update", async () => {
    const fact = { id: "fact-1", content: "公司通知解除", status: "confirmed", source: "user" };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(fact), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.updateFact("case-1", "fact-1", { status: "confirmed", occurred_on: null })).resolves.toEqual(fact);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/cases/case-1/facts/fact-1",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ status: "confirmed", occurred_on: null }) }),
    );
  });

  it("loads human review tasks for transparent risk status", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.listReviews("case-1")).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/cases/case-1/reviews",
      expect.any(Object),
    );
  });

  it("records explicit simulation consent with the required data scope", async () => {
    const consent = { id: "consent-1", purposes: ["simulation"], data_categories: ["conversation", "facts", "evidence_metadata"] };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(consent), { status: 201, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.grantModelConsent("case-1", {
      purposes: ["simulation"],
      data_categories: ["conversation", "facts", "evidence_metadata"],
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/cases/case-1/model-consents",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          provider: "deepseek",
          purposes: ["simulation"],
          data_categories: ["conversation", "facts", "evidence_metadata"],
        }),
      }),
    );
  });

  it("opens the active simulation instead of creating a new session on re-entry", async () => {
    const session = { id: "simulation-1", status: "active", transcript: [], feedback: [] };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(session), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.simulate("case-1")).resolves.toEqual(session);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/cases/case-1/simulations/active",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ scenario: "arbitration", user_role: "worker" }),
      }),
    );
  });

  it("only completes a simulation through the explicit completion action", async () => {
    const session = { id: "simulation-1", status: "completed", transcript: [], feedback: [] };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(session), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.completeSimulation("simulation-1")).resolves.toEqual(session);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/simulations/simulation-1/complete",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("adds intake consent without dropping an existing simulation scope", () => {
    const existing = {
      id: "consent-1",
      case_id: "case-1",
      provider: "deepseek",
      purposes: ["simulation"],
      data_categories: ["conversation", "facts", "evidence_metadata"],
      status: "active",
      version: 1,
      granted_at: new Date().toISOString(),
    };

    const merged = mergeConsentScope(existing, "intake");

    expect(merged.purposes).toEqual(["simulation", "intake"]);
    expect(merged.data_categories).toEqual(["conversation", "facts", "evidence_metadata"]);
  });

  it("removes duplicate model numbering from existing assistant messages", () => {
    expect(normalizeConsultationContent("为了进一步判断，请补充：\n1. 1. 是否有解除通知？\n2. 2、是否签订合同？"))
      .toBe("为了进一步判断，请补充：\n1. 是否有解除通知？\n2. 是否签订合同？");
  });

  it("puts inline numbered model points on separate lines", () => {
    expect(normalizeConsultationContent("建议：1. 先确认解除日期 2. 再核对工资流水"))
      .toBe("建议：\n1. 先确认解除日期\n2. 再核对工资流水");
  });

  it("separates compact and emphasized numbered arguments from simulation agents", () => {
    const response = "我方答辩如下：**1.** 劳动者主张无依据。2.录音系私自录制。 3、通知书仍需核实。";

    expect(normalizeConsultationContent(response)).toBe(
      "我方答辩如下：\n1. 劳动者主张无依据。\n2.录音系私自录制。\n3、通知书仍需核实。",
    );
  });

  it("streams simulation agents, tokens, counsel suggestions, and completion", async () => {
    const stream = [
      "event: status\ndata: {\"label\":\"正在回应\"}",
      "event: agent_start\ndata: {\"stream_id\":\"line-1\",\"role\":\"仲裁员\",\"agent_id\":\"arbitrator\",\"content\":\"\"}",
      "event: agent_token\ndata: {\"stream_id\":\"line-1\",\"content\":\"请说明\"}",
      "event: agent_complete\ndata: {\"stream_id\":\"line-1\",\"role\":\"仲裁员\",\"agent_id\":\"arbitrator\",\"content\":\"请说明请求\"}",
      "event: counsel\ndata: {\"feedback\":[\"先说请求\"],\"suggested_answers\":[\"我的请求是：[填写请求]\"]}",
      "event: complete\ndata: {\"session\":{\"id\":\"simulation-1\",\"status\":\"active\",\"transcript\":[],\"feedback\":[],\"suggested_answers\":[]}}",
    ].join("\n\n") + "\n\n";
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const handlers = {
      onStatus: vi.fn(),
      onAgentStart: vi.fn(),
      onAgentToken: vi.fn(),
      onAgentComplete: vi.fn(),
      onCounsel: vi.fn(),
      onComplete: vi.fn(),
    };

    await api.streamSimulationMessage("simulation-1", "我的回答", handlers);

    expect(handlers.onAgentStart).toHaveBeenCalledWith(expect.objectContaining({ stream_id: "line-1" }));
    expect(handlers.onAgentToken).toHaveBeenCalledWith("line-1", "请说明");
    expect(handlers.onCounsel).toHaveBeenCalledWith(["先说请求"], ["我的请求是：[填写请求]"]);
    expect(handlers.onComplete).toHaveBeenCalled();
  });
});
