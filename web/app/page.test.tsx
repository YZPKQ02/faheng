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
});
