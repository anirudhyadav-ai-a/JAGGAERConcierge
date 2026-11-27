import { describe, expect, it } from "vitest";
import type { ChatResponse } from "../api";
import { compare, statusLabel } from "../compare";

const resp = (over: Partial<ChatResponse> & { agents?: string[]; tools?: string[] } = {}): ChatResponse => ({
  session_id: "s",
  trace_id: "t",
  status: over.status ?? "ok",
  response: over.response ?? "same answer",
  path: "full",
  context: {
    agents: (over.agents ?? ["A13", "A14"]).map((agent_id) => ({ agent_id })),
    tools_used: over.tools ?? ["search_policies", "spending_summary"],
    llm_calls: 6,
    cost_usd: 0,
  },
});

describe("compare", () => {
  it("matches when agents, tools and answer are the same, regardless of order", () => {
    const c = compare(resp({ agents: ["A14", "A13"], tools: ["spending_summary", "search_policies"] }), resp());
    expect(c.match).toBe(true);
    expect(c.headline).toMatch(/^Both sides match/);
  });

  it("explains wording differences when only the text differs", () => {
    const c = compare(resp({ response: "phrased one way" }), resp({ response: "phrased another" }));
    expect(c.match).toBe(true);
    expect(c.headline).toMatch(/wording differs/);
  });

  it("flags a wrong selection", () => {
    const c = compare(resp({ agents: ["A14"], tools: ["spending_summary"] }), resp());
    expect(c.match).toBe(false);
    expect(c.sameAgents).toBe(false);
    expect(c.headline).toMatch(/didn't match what the question needed/);
  });

  it("calls out a clarifying question on the platform side", () => {
    const c = compare(resp({ agents: ["A11"] }), resp({ status: "clarify", agents: [], tools: [] }));
    expect(c.match).toBe(false);
    expect(c.headline).toMatch(/asked what you meant/);
  });
});

describe("statusLabel", () => {
  it("maps every backend status to a label", () => {
    expect(statusLabel("ok").label).toBe("Answered");
    expect(statusLabel("clarify").label).toBe("Asked you to clarify");
    expect(statusLabel("partial").label).toBe("Partly answered");
    expect(statusLabel("budget_exceeded").label).toBe("Spending limit reached");
    expect(statusLabel("something_new")).toEqual({ label: "something_new", tone: "error" });
  });
});
